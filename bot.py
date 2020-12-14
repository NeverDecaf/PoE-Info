#!/usr/bin/env python3.6
import discord
from discord.ext import commands,tasks
import asyncio
import os
import db
import re
from functools import wraps,partial
import sqlite3
import time
from collections import OrderedDict
import requests
import urllib.parse as urlparse
from lxml import html as lxmlhtml
import datetime
import io,shutil # for copying pins
import json,hashlib # for daily deal
from fractions import Fraction
import math
import dateparser
from dateparser.search import search_dates
from urllib.parse import quote as urlquote
from scrape_poe_wiki import get_lab_urls
from enum import Enum
abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)
DISCORD_PIN_LIMIT = 50
MESSAGE_EDITABLE_TIMEOUT = 60*60*24 # seconds, max of 1 day.
PRIVATE_CHANNEL = discord.ChannelType.private
SEARCH_REACTION_LIMIT = 9 # max digit emojis to show.
DIGIT_EMOJI = ['\U00000031\U000020E3',
                '\U00000032\U000020E3',
                '\U00000033\U000020E3',
                '\U00000034\U000020E3',
                '\U00000035\U000020E3',
                '\U00000036\U000020E3',
                '\U00000037\U000020E3',
                '\U00000038\U000020E3',
                '\U00000039\U000020E3']
def char_to_emoji(letter):
    return chr(127365 + ord(letter.lower()))
class Quality(Enum):
    NORMAL = 1
    ANOMALOUS = 2
    DIVERGENT = 3
class BotWithReactions(commands.Bot):
    DELETE_EMOJI = '\U0000274C'
    REACTION_TIMEOUT = 60*60*12 # seconds (was 300)
    REACTIONBUTTONS={}
    EMBEDPAGES = {}
    CLEANUP_TIMEOUT = 60 # seconds
    AUTO_CLEANUP = OrderedDict()
    CLEANUP_KEY = 0
    DEFAULT_FAILURE_MSG = '```No Results.```'
    async def send_failure_message(self,destination,failure_message=DEFAULT_FAILURE_MSG,message=None,**kwargs):
        ''' message is the user message the bot is replying to. if provided we can autodelete failure messages if the original is edited. '''
        sent_msg = await destination.send(content=failure_message, **kwargs)
        if not destination.type == PRIVATE_CHANNEL:
            self.AUTO_CLEANUP[message or self.CLEANUP_KEY] = (time.time(),sent_msg)
            self.CLEANUP_KEY = (self.CLEANUP_KEY+1)%1000000
        return sent_msg
    async def send_file(self, destination, fp, failure_message=DEFAULT_FAILURE_MSG, filename='file.png', **kwargs):
        if fp:
            sent_msg = await destination.send(file=discord.File(fp, filename),**kwargs)
        else:
            sent_msg = await self.send_failure_message(destination,failure_message = failure_message)
        return sent_msg
    async def send_message(self, destination, content=None, failure_message=DEFAULT_FAILURE_MSG, code_block=True, **kwargs):
        if content and code_block:
            content = '```'+content.strip('`').rstrip('`')+'```' # turn our message into a code block.
        if content or kwargs.get('embed'): # if message is blank and no embed, send failure message instead
            sent_msg = await destination.send(content=content, **kwargs)
        else:
            sent_msg = await self.send_failure_message(destination,failure_message = failure_message)
        return sent_msg
    async def send_deletable_file(self,author,*args,**kwargs):
        '''
        attaches a X reaction that allows the requester (author) to delete the sent file

        only works in public channels. in PMs the message will be sent as normal.
        '''
        sent_msg = await self.send_file(*args, **kwargs)
        if isinstance(args[0],discord.abc.GuildChannel) and not sent_msg.content == self.DEFAULT_FAILURE_MSG:
            await self.attach_button(sent_msg,author,self.DELETE_EMOJI,lambda x,y,z:self.delete_message(x))
        return sent_msg
    async def send_deletable_message(self,author,*args, code_block = True, **kwargs):
        '''
        attaches a X reaction that allows the requester (author) to delete the sent message

        only works in public channels. in PMs the message will be sent as normal.
        '''
        sent_msg = await self.send_message(*args, code_block=code_block, **kwargs)
        if isinstance(args[0],discord.abc.GuildChannel) and not sent_msg.content == self.DEFAULT_FAILURE_MSG:
            await self.attach_button(sent_msg,author,self.DELETE_EMOJI,lambda x,y,z:self.delete_message(x))
        return sent_msg
    async def attach_button(self, message, author, emoji, callback, *data, user_restricted=True, single_use=False, **kwargs):
        '''Add a reaction button. When pressed callback will be called with message,author,*data as arguments.'''
        try:
            key = (message.id,emoji)
            if key not in self.REACTIONBUTTONS:
                await message.add_reaction(emoji)
            if not user_restricted:
                author = None
            self.REACTIONBUTTONS[key]=(time.time(),callback,message,single_use,author,data,kwargs)
        except discord.errors.NotFound:
            pass # this one means the message/reaction was deleted already so no big deal just ignore
    async def remove_button(self, msg, emoji):
        ''' removes an emojibutton, key is (msg.id,emoji) '''
        key = (msg.id,emoji)
        self.REACTIONBUTTONS.pop(key,None)
        try:
            await self.remove_all_reactions(msg,emoji)
        except (discord.errors.NotFound, discord.errors.Forbidden):
            # this one means the message/reaction was deleted already so no big deal just ignore
            # Forbidden will occur if trying to delete other users emotes without permission
            pass
    async def create_paged_embed(self,author,channel,pages,default_page):
        #pages is a dict of emoji:embed
        # default_page is the key of the page that should be shown first
        sent_msg = await self.send_deletable_message(author, channel, embed = pages[default_page])
        self.EMBEDPAGES[sent_msg] = pages
        for emoji in pages.keys():
            await self.attach_button(sent_msg, author, emoji, self._swap_embed_page, emoji)
        return sent_msg
    async def _swap_embed_page(self, msg, author, remove, key):
        await msg.edit(embed=self.EMBEDPAGES[msg][key])
        return
        
    async def auto_cleanup(self):
        now = time.time()
        while len(self.AUTO_CLEANUP):
            key = next(iter(self.AUTO_CLEANUP))
            if now - self.AUTO_CLEANUP[key][0] > self.CLEANUP_TIMEOUT:
                _,(_,msg) = self.AUTO_CLEANUP.popitem()
                await self.delete_message(msg)
            else:
                break
    async def edited_cleanup(self,msg):
        if msg in self.AUTO_CLEANUP:
            _,todel = self.AUTO_CLEANUP[msg]
            del self.AUTO_CLEANUP[msg] # there is a race condition here as auto cleanup can occur in the same instant as this del
            await self.delete_message(todel)
            
    async def process_reactions(self,message_id,emoji,new_author=None,remove=False):
        '''call this in on_reaction_add. For non-restricted buttons new_author must be passed (this will be the user allowed to delete the new message)
           After a reaction is pressed the button/reaction will be removed.'''
        key = (message_id, emoji)
        if key in self.REACTIONBUTTONS:
            emoji = key[1]
            _,callback,msg,single_use,author,data,kwargs=self.REACTIONBUTTONS[key]
            if author != None and author != new_author:
                return
            if single_use:
                self.REACTIONBUTTONS.pop(key,None)
                try:
                    await self.remove_all_reactions(msg,emoji)
                except discord.errors.NotFound:
                    pass # this one means the message/reaction was deleted already so no big deal just ignore
            if new_author:
                await callback(msg,new_author,remove,*data,**kwargs)
            else:
                await callback(msg,author,remove,*data,**kwargs)
                
    async def remove_stale_reactions(self):
        '''Run this every ~1 second in a background loop. This simply removes reactions that have expired. (set REACTION_TIMEOUT)'''
        now = time.time()
        for key in list(self.REACTIONBUTTONS.keys()):
            emoji = key[1]
            msg_timestamp,_,msg,*_=self.REACTIONBUTTONS[key]
            if now-msg_timestamp>self.REACTION_TIMEOUT:
                self.REACTIONBUTTONS.pop(key,None)
                try:
                    await self.remove_all_reactions(msg,emoji)
                except discord.errors.NotFound:
                    pass # this one means the message/reaction was deleted already so no big deal just ignore
        for msg in list(self.EMBEDPAGES.keys()):
            datediff = (datetime.datetime.utcnow() - msg.created_at)
            if datediff.days*24*60*60 + datediff.seconds > self.REACTION_TIMEOUT:
                pages = self.EMBEDPAGES.pop(msg,[])
                for emoji in pages:
                    try:
                        await self.remove_all_reactions(msg,emoji)
                    except discord.errors.NotFound:
                        pass # this one means the message/reaction was deleted already so no big deal just ignore

    async def on_reaction_add(self,reaction,user):
        if reaction.me and user!=self.user and user is not None:
            await self.process_reactions(reaction.message.id,reaction.emoji,new_author=user)
    # async def on_reaction_remove(self,reaction,user):
        # if reaction.me and user!=self.user and user is not None:
            # await self.process_reactions(reaction.message.id,reaction.emoji,new_author=user,remove=True)
    # because on_reaction_remove does not register (message is not cached?)
    async def on_raw_reaction_remove(self,payload):
        if (payload.user_id is not None) and payload.user_id != self.user.id:
            await self.process_reactions(payload.message_id,payload.emoji.name,new_author=self.get_user(payload.user_id),remove=True)
    async def on_message_edit(self,before,after):
        datediff = (datetime.datetime.utcnow() - before.created_at)
        if before.content!=after.content and datediff.days*24*60*60+datediff.seconds<MESSAGE_EDITABLE_TIMEOUT: # need this check because auto-embed counts as editing
            await self.process_commands(after)
            try:
                await self.edited_cleanup(after) # this can error due to race condition
            except:
                pass
            
    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name=self.command_prefix+'help'))
        self.cleanup_reactions.start()
    @tasks.loop(seconds=60.0)
    async def cleanup_reactions(self):
        try:
            events = self.db.upcoming_event()
            nextevent = self.db.event_ending()
            if events or nextevent:
                r=self.cursor.execute('SELECT channel FROM announce WHERE type="event"')
                for channel in [i[0] for i in r.fetchall()]:
                    try:
                        if events:
                            for event in events:
                                await self.send_message(self.get_channel(channel), '%s'%str(event[0]))
                        if nextevent:
                            await self.send_message(self.get_channel(channel), 'diff\n%s'%nextevent)
                    except:
                        'channel missing or bot is blocked'
            await self.remove_stale_reactions()
            await self.auto_cleanup()
        except:
            'just for extra safety because an error here means the loop stops'
    @cleanup_reactions.before_loop
    async def before_run(self):
        await self.wait_until_ready()
    
    # compatibility funcs for discord 0.9
    async def delete_message(self, msg):
        ''' for backwards compatibility '''
        return await msg.delete()
    async def remove_all_reactions(self, msg, emo):
        ''' for backwards compatibility '''
        cache_msg = discord.utils.get(self.cached_messages, id=msg.id)
        for r in [m for m in cache_msg.reactions if m.emoji==emo]:
            try:
                await r.clear()
            except discord.errors.Forbidden:
                'missing permission to remove emojis'
    async def remove_reaction(self, msg, emo, user):
        ''' for backwards compatibility '''
        return await msg.remove_reaction(emo,user)
    async def unpin_message(self, msg):
        ''' for backwards compatibility '''
        return await msg.unpin()
    async def pins_from(self, channel):
        ''' for backwards compatibility '''
        return await channel.pins()
    async def edit_message(self, msg, **fields):
        ''' for backwards compatibility '''
        await msg.edit(**fields)
        return msg
            
bot = BotWithReactions(command_prefix='-', description='PoE Info.')

@bot.listen()
async def on_guild_channel_pins_update(chan, last_pin):
    try:
        pins = await bot.pins_from(chan)
    except (discord.errors.NotFound, discord.errors.Forbidden):
        'missing permissions'
        return
    if len(pins) >= DISCORD_PIN_LIMIT:
        r=bot.cursor.execute('SELECT dest FROM pins WHERE source=?',(chan.id,))
        dest = r.fetchone()
        if dest:
            pin_channel=bot.get_channel(dest[0])
            if pin_channel and _pin_perm_check(chan.guild, chan, pin_channel):
                await _move_pins(pins[-1:], pin_channel)

@bot.listen()
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')
    
@bot.command(pass_context=True)
async def pin(ctx, *count : str):
    '''<count>|<set>
Number of pins to move OR set a channel for pins.'''
    if not ctx.message.author.permissions_in(ctx.message.channel).administrator:
        await bot.send_message(ctx.message.channel, 'You must be an administrator to use this command.')
        return
    if len(count)>1 and count[0] == 'set':
        # set pin channel
        if not ctx.message.author.permissions_in(ctx.message.channel).administrator:
            await bot.send_message(ctx.message.channel, 'You must be an administrator to set pin channel.')
            return
        try:
            just_id = count[1][2:-1]
            ch = bot.get_channel(int(just_id))
            if ch and _pin_perm_check(ctx.message.guild,ctx.message.channel,ch):
                bot.cursor.execute('REPLACE INTO pins(source,dest) VALUES(?,?)',(ctx.message.channel.id,just_id))
                bot.conn.commit()
                await bot.send_message(ctx.message.channel, 'Set pin destination for {} to {}'.format(ctx.message.channel.mention,ch.mention),code_block=False)
                return
            else:
                raise Exception()
        except:
            await bot.send_message(ctx.message.channel, 'Invalid pin channel (must be a channel on this server + bot must have proper permissions)')
            return
    try:
        if int(count[0])<=0:
            await bot.send_message(ctx.message.channel, 'usage:\n-pin <count>\n-pin set <channel>')
            return
    except:
        await bot.send_message(ctx.message.channel, 'usage:\n-pin <count>\n-pin set <channel>')
        return

    r=bot.cursor.execute('SELECT dest FROM pins WHERE source=?',(ctx.message.channel.id,))
    dest = r.fetchone()
    if not dest:
        await bot.send_message(ctx.message.channel, 'No destination channel set for pins, use -pin set <channel>')
        return
    pin_channel=bot.get_channel(dest[0])
    # do a extra permissions check for safety:
    if pin_channel and _pin_perm_check(ctx.message.guild,ctx.message.channel,pin_channel):
        pins = await bot.pins_from(ctx.message.channel)
        await _move_pins(list(reversed(pins))[:min(len(pins),int(count[0]))],pin_channel)
    else:
        await bot.send_message(ctx.message.channel, 'Invalid pin channel (must be a channel on this server + bot must have proper permissions)')
        return
def _pin_perm_check(server,src,dst):
    src_perms=src.permissions_for(server.me)
    dest_perms=dst.permissions_for(server.me)
    return dest_perms.send_messages and dest_perms.attach_files and dest_perms.embed_links\
           and src_perms.read_message_history and src_perms.manage_messages and src_perms.read_messages
async def _move_pins(pinlist,pin_channel):
    for pin in pinlist:
        e = _create_pin_embed(pin)
        await bot.send_message(pin_channel, code_block=False, embed = e)
        await bot.unpin_message(pin) # This isnt working, or pins_from isnt refreshsed
@bot.command(pass_context=True, aliases = ['remind','remindme'])
async def reminder(ctx, *args : str):
    '''<datetime/timedelta> <message>
Set a reminder; for example:
-reminder 3pm June 10 hello
-reminder 3 days 10 hours world
sub-commands:
reminder list - list all reminders for yourself
reminder delete <index> - delete specified reminder
reminder timezone <tz> - set timezone for date reminders'''
    helpmsg = 'usage:\n-reminder <datetime/timedelta> <message>'
    time_display_format = "%Y-%m-%d %H:%M:%S"
    isprivate = ctx.message.channel.type == PRIVATE_CHANNEL
    fulltext = ' '.join(args)
    if not len(args):
        await bot.send_message(ctx.message.channel, helpmsg)
        return
    server_id = ctx.message.guild and ctx.message.guild.id or ctx.message.channel.id
    subcmd = args[0]
    r = bot.cursor.execute('SELECT timezone from timezones where server=?',(server_id,)).fetchone()
    settings = {'TIMEZONE':(r and r[0]) or 'UTC', 'TO_TIMEZONE':'UTC', 'PREFER_DATES_FROM': 'future'}
    disp_settings = {'TO_TIMEZONE':(r and r[0]) or 'UTC', 'TIMEZONE':'UTC', 'PREFER_DATES_FROM': 'future'}
    def parse_reminder_time(txt):
        dates = search_dates(txt, settings = settings)
        for datestr,dt in dates:
            if txt.startswith(datestr):
                msg = txt[len(datestr):].lstrip()
                if dt < datetime.datetime.utcnow():
                    diff = datetime.datetime.utcnow() - dt
                    return datetime.datetime.utcnow() + diff, msg
                return dt, msg
        return None,None
    if subcmd in ('list','-l'):
        r = bot.cursor.execute('SELECT message,datetime FROM reminders where creator = ? and server = ? ORDER by datetime ASC',(ctx.message.author.id,server_id))
        res = r.fetchall()
        if not res:
            await bot.send_message(ctx.message.channel, 'You have 0 reminders.')
            return
        p = ''
        for i,r in enumerate(res):
            p+= '{}. "{}" on {}\n'.format(i,r[0],dateparser.parse(r[1],settings = disp_settings).strftime(time_display_format))
        await bot.send_message(ctx.message.channel, p)
    elif subcmd in ('delete','del'):
        if len(args)<2 or not re.match('^\d*$',args[1]):
            await bot.send_message(ctx.message.channel, 'usage:\n-reminder del <index>')
            return
        
        r = bot.cursor.execute('SELECT creator,role,channel,server,datetime,message FROM reminders where creator = ? and server = ? ORDER by datetime ASC',(ctx.message.author.id,server_id))
        res = r.fetchall()
        bot.cursor.execute('DELETE FROM reminders WHERE creator = ? and role = ? and channel = ? and server = ? and datetime = ? and message = ?', res[int(args[1])])
        bot.conn.commit()
        await bot.send_message(ctx.message.channel, 'Reminder deleted.')
    elif subcmd in ('timezone','tz'):
        if len(args)<2:
            await bot.send_message(ctx.message.channel, 'usage:\n-reminder timezone <timezone>')
            return
        if not isprivate and not ctx.message.author.permissions_in(ctx.message.channel).administrator:
            await bot.send_message(ctx.message.channel, 'You must be an administrator to set reminder timezone for this server.')
            return
        tz = args[1]
        bot.cursor.execute('REPLACE INTO timezones(server,timezone) VALUES(?,?)',(server_id,tz))
        bot.conn.commit()
        await bot.send_message(ctx.message.channel, 'Server timezone set to "{}"'.format(tz))
    # elif subcmd is role or channel, this isnt that useful, maybe implement later.
    # elif re.match('^@&.*$',subcmd):
        # if not ctx.message.author.permissions_in(ctx.message.channel).administrator:
            # await bot.send_message(ctx.message.channel, 'You must be an administrator to set reminders for a role')
            # return
        # print([role.name for role in ctx.message.guild.roles],subcmd[1:])
        # validroles = [role for role in ctx.message.guild.roles if role.name==subcmd[1:]]
        # if not validroles:
            # await bot.send_message(ctx.message.channel, 'Role not found.',code_block=False)
            # return
        # await bot.send_message(ctx.message.channel, 'channel, @{}'.format(validroles[0]),code_block=False)
    elif len(args)>1:
        date,msg = parse_reminder_time(fulltext)
        if not date:
            await bot.send_message(ctx.message.channel, helpmsg)
            return
        if date.tzinfo:
            await bot.send_message(ctx.message.channel, 'timezone argument not (currently) supported, set global timezone for this server with -reminder timezone <tz>')
            return
        if date <= datetime.datetime.utcnow():
            await bot.send_message(ctx.message.channel, 'Given date ({}) has already passed, try being more specific.'.format(date))
            return
        bot.cursor.execute('REPLACE INTO reminders(creator,server,channel,datetime,message) VALUES(?,?,?,?,?)',(ctx.message.author.id,server_id,ctx.message.channel.id,date,msg))
        bot.conn.commit()
        await bot.send_message(ctx.message.channel, 'reminder set for {}'.format(dateparser.parse(date.strftime("%Y-%m-%d %H:%M:%S.%f"),settings = disp_settings).strftime(time_display_format)))
    else:
        await bot.send_message(ctx.message.channel, helpmsg)
    return
        
async def announce_internals(ctx,msg,announce_id,announce_name,commandname):
    destination = ctx.message.channel
    if not msg or not len(msg):
        r=bot.cursor.execute('SELECT 1 FROM announce WHERE channel=? AND type=?',(destination.id,announce_id))
        enabled = r.fetchone()
        if destination.type == PRIVATE_CHANNEL:
            await bot.send_message(destination, '{} {}.'.format(announce_name,'enabled' if enabled else 'not enabled'))
        else:
            await bot.send_message(destination, '{} {} for {}.'.format(announce_name,'enabled' if enabled else 'not enabled',destination.mention), code_block=False)
        return
    if not destination.type == PRIVATE_CHANNEL and not ctx.message.author.permissions_in(ctx.message.channel).administrator:
        await bot.send_message(destination, 'You must be an administrator to use this command.')
        return
    if msg in ('on','off'):
        if msg == 'on':
            bot.cursor.execute('REPLACE INTO announce (channel,type) VALUES (?,?)',(destination.id,announce_id))
            bot.conn.commit()
            if destination.type == PRIVATE_CHANNEL:
                await bot.send_message(destination, '{} enabled.'.format(announce_name))
            else:
                await bot.send_message(destination, '{} enabled for {}.'.format(announce_name,destination.mention), code_block=False)
        else:
            bot.cursor.execute('DELETE FROM announce WHERE channel=? AND type=?',(destination.id,announce_id))
            bot.conn.commit()
            if destination.type == PRIVATE_CHANNEL:
                await bot.send_message(destination, '{} disabled.'.format(announce_name))
            else:
                await bot.send_message(destination, '{} disabled for {}.'.format(announce_name,destination.mention), code_block=False)
    else:
        await bot.send_message(destination, 'usage: -{} <on|off>'.format(commandname))

@bot.command(pass_context=True,aliases=['setleague','league','pricecheck'])
async def pcleague(ctx, *league : str):
    '''<league>
Set league for pricing in this channel, options are: tmpStandard, tmpHardcore, eventStandard, eventHardcore, Standard, Hardcore.'''
    destination = ctx.message.channel
    if not league or not len(league):
        r=bot.cursor.execute('SELECT league FROM pricecheck WHERE channel=?',(destination.id,))
        league = (r.fetchone() or ('tmpStandard',))[0]
        if destination.type == PRIVATE_CHANNEL:
            await bot.send_message(destination, 'Currently checking prices in {}. -help pcleague to change.'.format(league,))
        else:
            await bot.send_message(destination, 'Currently checking prices in {} for {}. -help pcleague to change.'.format(league,destination.mention), code_block=False)
        return
    if not destination.type == PRIVATE_CHANNEL and not ctx.message.author.permissions_in(ctx.message.channel).administrator:
        await bot.send_message(destination, 'You must be an administrator to use this command.')
        return
    try:
        i = [a.lower() for a in db.VALID_PC_LEAGUES].index(' '.join(league).lower())
        bot.cursor.execute('REPLACE INTO pricecheck (channel,league) VALUES (?,?)',(destination.id,db.VALID_PC_LEAGUES[i]))
        bot.conn.commit()
        await bot.send_message(destination, 'Now pricechecking in {}.'.format(db.VALID_PC_LEAGUES[i]))
    except ValueError:
        await bot.send_message(destination, 'Not a valid league, must be one of: tmpStandard, tmpHardcore, eventStandard, eventHardcore, Standard, Hardcore')
class Alerts(commands.Cog):
    '''Toggle on/off automatic annoucements of the following:'''
    @commands.command(pass_context=True)
    async def announcements(self, ctx, *toggle : str):
        '''<on|off>
    Turn forum announcements on/off.'''
        await announce_internals(ctx,' '.join(toggle),'forumannounce','Forum news announcements','announcements')

    @commands.command(pass_context=True,aliases=['patchnote'])
    async def patchnotes(self, ctx, *toggle : str):
        '''<on|off>
    Turn patch note posts on/off.'''
        await announce_internals(ctx,' '.join(toggle),'patchnotes','Patch note announcements','patchnotes')
        
    @commands.command(pass_context=True,aliases=['daily_deals'])
    async def deals(self, ctx, *toggle : str):
        '''<on|off|filter>
    Turn daily deal announcements on/off.
    Add a regexp filter with -deals filter <regexp>
    use the regexp: .* to show all deals.'''
        destination = ctx.message.channel
        if not destination.type == PRIVATE_CHANNEL and not ctx.message.author.permissions_in(ctx.message.channel).administrator:
            await bot.send_message(destination, 'You must be an administrator to use this command.')
            return
        if toggle:
            if (toggle[0] not in ('filter','on','off')):
                await bot.send_message(destination, 'usage: -{} <on|off|filter>'.format('deals'))
                return
            elif toggle[0] == 'filter':
                if len(toggle)<2:
                    r = bot.cursor.execute('SELECT regexp FROM regexp_filters WHERE channel=? AND type=?',(ctx.message.channel.id,'dailydeal'))
                    res = r.fetchone()
                    await bot.send_message(destination, 'Current filter is: {}'.format(res[0] if res else '.*'))
                else:
                    r = bot.cursor.execute('REPLACE INTO regexp_filters (channel,type,regexp) VALUES (?,?,?)',(ctx.message.channel.id,'dailydeal',toggle[1]))
                    bot.conn.commit()
                    await bot.send_message(destination, 'Filter set to: {}'.format(toggle[1]))
                return
        await announce_internals(ctx,' '.join(toggle),'dailydeal','Daily deal announcements','deals')
        
    @commands.command(pass_context=True)
    async def events(self, ctx, *toggle : str):
        '''<on|off>
    Turn event announcements on/off.'''
        await announce_internals(ctx,' '.join(toggle),'event','Event announcements','events')
class Info(commands.Cog):
    'Show info on in-game items. These commands have one letter aliases for quicker use (ex: -u)'
    @commands.command(pass_context=True,aliases=['u','pc','us'])
    async def unique(self, ctx, *itemname: str):
        '''<itemname>
    Shows stats for an item. Partial names acceptable.
    search <key words> (alias: -us)
    Search for items whose explicit mods contain ALL keywords.'''
        if not len(itemname):
            await bot.send_message(ctx.message.channel, 'usage: -u <item name>')
            return
        # consider showing flavor text in the embed footer
        item = ' '.join(itemname)
        r=bot.cursor.execute('SELECT league FROM pricecheck WHERE channel=?',(ctx.message.channel.id,))
        league = (r.fetchone() or ('tmpStandard',))[0]
        
        if itemname[0].lower() == 'search' or ctx.invoked_with == 'us':
            if (len(itemname) + (ctx.invoked_with == 'us'))<2:
                await bot.send_message(ctx.message.channel, 'usage: -us <key words>')
                return
            data = bot.db.unique_search_explicit(itemname[(ctx.invoked_with != 'us'):],league)
            if not data:
                await bot.send_failure_message(ctx.message.channel)
                return
            if len(data)>1:
                #send choices
                sent_msg= await bot.send_message(ctx.message.channel, 'Multiple Results:\n'+'\n'.join(['%i. %s'%(i+1,datum['name']) for i,datum in enumerate(data)]))
                for i in range(min(SEARCH_REACTION_LIMIT,len(data))):
                    await bot.attach_button(sent_msg, ctx.message.author, DIGIT_EMOJI[i], _search_result, data[i], _create_unique_embed)#, _search_result, data[i][3])
                return
            e = _create_unique_embed(data[0])
            await bot.send_deletable_message(ctx.message.author, ctx.message.channel, embed=e)
            return
        
        data = bot.db.get_data('unique_items',item,league)
        if not data:
            data = bot.db.get_data('unique_items',item,league,search_by_baseitem=True)
            if not data:
                await bot.send_failure_message(ctx.message.channel)
                return
        if len(data)>1:
            #send choices
            sent_msg= await bot.send_message(ctx.message.channel, 'Multiple Results:\n'+'\n'.join(['%i. %s'%(i+1,datum['name']) for i,datum in enumerate(data)]))
            for i in range(min(SEARCH_REACTION_LIMIT,len(data))):
                await bot.attach_button(sent_msg, ctx.message.author, DIGIT_EMOJI[i], _search_result, data[i], _create_unique_embed)#, _search_result, data[i][3])
            return
        e = _create_unique_embed(data[0])
        await bot.send_deletable_message(ctx.message.author, ctx.message.channel, embed=e)

    @commands.command(pass_context=True)
    async def lab(self, ctx, *difficulty: str):
        '''[<difficulty>]
        Displays map for current uber lab, or difficulty if specified (one of uber,merciless,cruel,normal)'''
        if not len(difficulty) or not difficulty[0] in ('normal','cruel','merciless','uber','merc'):
            diff = 'uber'
        else:
            diff = difficulty[0]
        if diff == 'merc':
            diff = 'merciless'
        today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        r=bot.cursor.execute('select img_url from daily_labs where diff=? and date=?',(diff,today))
        data = r.fetchone()
        if not data:
            _cache_labs()
            r=bot.cursor.execute('select img_url from daily_labs where diff=? and date=?',(diff,today))
            data = r.fetchone()
        if not data:
            return await bot.send_failure_message(ctx.message.channel)
        #'https://www.poelab.com/wp-content/labfiles/{}_{}.jpg'.format(datetime.datetime.utcnow().strftime('%Y-%m-%d'), diff)
        await bot.send_message(ctx.message.channel, data[0], code_block = False)
            
    @commands.command(pass_context=True,aliases=['s'])
    async def skill(self, ctx, *skill_name: str):
        '''<skill>
    Shows stats for a skill gem. Partial names acceptable.'''
        if not len(skill_name):
            await bot.send_message(ctx.message.channel, 'usage: -s <skill gem name>')
            return
        # consider showing flavor text in the embed footer
        item = ' '.join(skill_name)
        r=bot.cursor.execute('SELECT league FROM pricecheck WHERE channel=?',(ctx.message.channel.id,))
        league = (r.fetchone() or ('tmpStandard',))[0]
        data = bot.db.get_data('skill_gems',item,league)
        if not data:
            await bot.send_failure_message(ctx.message.channel)
            return
        if len(data)>1:
            #send choices
            sent_msg= await bot.send_message(ctx.message.channel, 'Multiple Results:\n'+'\n'.join(['%i. %s'%(i+1,datum['name']) for i,datum in enumerate(data)]))
            for i in range(min(SEARCH_REACTION_LIMIT,len(data))):
                await bot.attach_button(sent_msg, ctx.message.author, DIGIT_EMOJI[i], _search_result, data[i], _create_gem_embed)#, _search_result, data[i][3])
            return
        await self._skill_internals(ctx, data[0])
        
    async def _skill_internals(self, ctx, data):
        pages = {
        char_to_emoji('n'):_create_gem_embed(data,quality = Quality.NORMAL)
        }
        if data['qual_bonus_anomalous']:
            pages[char_to_emoji('a')] = _create_gem_embed(data,quality = Quality.ANOMALOUS)
        if data['qual_bonus_divergent']:
            pages[char_to_emoji('d')] = _create_gem_embed(data,quality = Quality.DIVERGENT)
        if len(pages)>1:
            await bot.create_paged_embed(ctx.message.author, ctx.message.channel, pages, char_to_emoji('n'))
        else:
            await bot.send_deletable_message(ctx.message.author, ctx.message.channel, embed=pages[char_to_emoji('n')])

        
    @commands.command(pass_context=True,aliases=['c'])
    async def currency(self, ctx, *currency_name: str):
        '''<name>
    Shows exchange rate for a currency item. Partial names acceptable.'''
        if not len(currency_name):
            await bot.send_message(ctx.message.channel, 'usage: -c <currency name>')
            return
        # consider showing flavor text in the embed footer
        item = ' '.join(currency_name)
        r=bot.cursor.execute('SELECT league FROM pricecheck WHERE channel=?',(ctx.message.channel.id,))
        league = (r.fetchone() or ('tmpStandard',))[0]
        data = bot.db.get_currency(item,league)
        if not data:
            await bot.send_failure_message(ctx.message.channel)
            return
        if len(data)>1:
            #send choices
            sent_msg= await bot.send_message(ctx.message.channel, 'Multiple Results:\n'+'\n'.join(['%i. %s'%(i+1,datum['name']) for i,datum in enumerate(data)]))
            for i in range(min(SEARCH_REACTION_LIMIT,len(data))):
                await bot.attach_button(sent_msg, ctx.message.author, DIGIT_EMOJI[i], _search_result, data[i], _create_currency_embed)#, _search_result, data[i][3])
            return
        e = _create_currency_embed(data[0])
        await bot.send_deletable_message(ctx.message.author, ctx.message.channel, embed=e)
def _cache_labs():
    today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    for lab,url in zip(('normal','cruel','merciless','uber'),get_lab_urls(today)):
        if url:
            bot.cursor.execute('REPLACE INTO daily_labs (date,diff,img_url) VALUES (?,?,?)',(today,lab,url))
    bot.cursor.execute('DELETE FROM daily_labs WHERE date <> ?',(today,))
    bot.conn.commit()
async def _search_result(msg, author, remove, data, _func):
    e = _func(data)
    await bot.send_deletable_message(author, msg.channel, embed=e)
    await bot.delete_message(msg)
    
@bot.command(pass_context=True,aliases=['nextrace','nextevent'])
async def next(ctx):
    '''Displays the upcoming race.'''
    nextmsg = bot.db.next_event()
    if nextmsg:
        await bot.send_message(ctx.message.channel, '%s'%nextmsg)
    else:
        await bot.send_message(ctx.message.channel, 'No upcoming events.')

def _strip_html_tags(text):
    return re.sub(r'<(?!One to)[^>]+>','',re.sub(r'<(br|tr|hr)[^>]+>','\n',re.sub(r' \| ','\n',text)),flags=re.I)
    
def _create_currency_embed(data):
    price = data['chaosValue']
    exaltValue = bot.db.get_currency('Exalted Orb',data['league'], exact=True)[0]['chaosValue']
    chaos_to_spend = 20
    limit = math.ceil(chaos_to_spend/price)
    if data['chaosValue'] > exaltValue * 2:
        stats_string = 'Est. Price: **{}**c\napprox. **{:.1f}**ex'.format(price, price/exaltValue)
    else:
        frac = Fraction(data['chaosValue']).limit_denominator(int(limit))
        stats_string = 'Est. Price: **{}**c\napprox. **{}** : **{}**c'.format(price,frac.denominator,frac.numerator)
    e = discord.Embed(url='https://pathofexile.gamepedia.com/{}'.format(data['name'].replace(' ','_')),
        description=_strip_html_tags(stats_string),
        title=data['name'].strip(),
        type='rich',color=0x638000)
    if 'icon' in data.keys() and data['icon']:
        e.set_thumbnail(url=data['icon'].replace(' ','%20'))
    return e

def _create_unique_embed(data):
    def if_not_zero(val,label):
        if val and val!='0':
            return label+' '+val+'\n'
        return ''
    def stat_not_zero(val,stat):
        if val and val!='0':
            return val+' '+stat
        return ''
    def stat_not_one(val,stat):
        if val and val!='1':
            return stat+' '+val
        return ''
    bold_nums = re.compile('(\(?-?(?:\d+(?:-|(?: to )))?\d*\.?\d+\)?%?)')
    bold_nums = re.compile('(\(?-?(?:\d*\.?\d+(?:-|(?: to )))?\d*\.?\d+\)?%?)')
    if 'chaosValue' in data.keys() and 'exaltedValue' in data.keys() and data['chaosValue'] is not None and data['exaltedValue'] is not None:
        if data['exaltedValue'] > 1:
            stats_string = 'Est. Price: {0:.1f}ex\n'.format(data['exaltedValue'])
        else:
            stats_string = 'Est. Price: {0:.0f}c\n'.format(data['chaosValue'])
    else:
        stats_string = ''
    # leading \n are automatically removed by discord. you can use this to your advantage if you're careful how you place these.
    stats_string+="{}".format(if_not_zero(data['block'],'Chance to Block:'))
    #stats for armour pieces:
    stats_string+="{}{}{}".format(if_not_zero(data['armour'],'Armour:'), if_not_zero(data['eva'],'Evasion:'), if_not_zero(data['es'],'Energy Shield:'))
    #stats for weapons
    for dtype in [('Physical Damage:','phys'),('Fire Damage:','fire'),('Cold Damage:','Cold'),('Lightning Damage:','light'),('Chaos Damage:','chaos')]:
        if data[dtype[1]+'max'] and data[dtype[1]+'max']!='0':
            stats_string+='{} {}-{}\n'.format(dtype[0],data[dtype[1]+'min'],data[dtype[1]+'max'])
    stats_string+="{}{}{}".format(if_not_zero(data['crit'],'Critical Strike Chance:'), if_not_zero(data['aspd'],'Attacks per Second:'), if_not_zero(data['range'],'Weapon Range:'))
    #stats for flasks
    if data['flaskduration']:
        stats_string+="Lasts {} Seconds\n".format(data['flaskduration'])
##        stats_string+="Lasts {0:.2f} Seconds\n".format(float(data['flaskduration']))
        stats_string+="Consumes {} of {} charges on use\n".format(data['flaskchargesused'],data['flaskcharges'])
    # level and stat requirements
    reqs = [s for s in [stat_not_one(data['levelreq'],'Level'),stat_not_zero(data['strreq'],'Str'),stat_not_zero(data['dexreq'],'Dex'),stat_not_zero(data['intreq'],'Int')] if s]
    if reqs and data['name']!='Tabula Rasa':
        stats_string+='Requires {}'.format(', '.join(reqs))
    stats_string+='{}'.format(if_not_zero(data['jewellimit'],'Limited To:'))
    stats_string+='{}'.format(data['jewelradius'])
    stats_string=bold_nums.sub(r'**\1**', stats_string).replace('****','')
    e = discord.Embed(url='https://pathofexile.gamepedia.com/{}'.format(data['name'].replace(' ','_')),
        description=_strip_html_tags(stats_string),
        title='\n'.join((data['name'].strip(),data['baseitem'].strip())),
        type='rich',color=0xaf6025)
    if 'icon' in data.keys() and data['icon']:
        e.set_thumbnail(url=data['icon'])
    elif 'image_url' in data.keys() and data['image_url']:
        e.set_thumbnail(url='https://pathofexile.gamepedia.com/Special:Redirect/file/{}'.format(urlquote(data['image_url'])))
    if data['impl'] or data['expl']: #this is only for tabula
        e.add_field(name=(_strip_html_tags(bold_nums.sub(r'**\1**', str(data['impl']))) or '--').replace('****',''),value=(_strip_html_tags(bold_nums.sub(r'**\1**', str(data['expl']))) or '--').replace('****',''),inline=False)
    if data['physdps'] or data['eledps']:
        s=''
        if data['physdps']:
            s+="Physical DPS: {} ".format(data['physdps'])
        if data['eledps']:
            s+="Elemental DPS: {}".format(data['eledps'])
        e.set_footer(text=s)
    return e

def _create_gem_embed(data, quality=Quality.NORMAL):
    def if_not_zero(val,label):
        if val:
            return label+' '+val+'\n'
        return ''
    def stat_not_zero(val,stat):
        if val:
            return ', '+val+' '+stat
        return ''
    bold_nums = re.compile('(\(?-?(?:\d*\.?\d+(?:-|(?: to )))?\d*\.?\d+\)?%?)')

    if 'chaosValue' in data.keys() and 'exaltedValue' in data.keys() and data['chaosValue'] is not None and data['exaltedValue'] is not None:
        if data['exaltedValue'] > 1:
            if data['name'].startswith('Vaal '):
                stats_string = '20/20 Price: {0:.1f}ex\n'.format(data['exaltedValue'])
            else:
                stats_string = '20q Price: {0:.1f}ex\n'.format(data['exaltedValue'])
        else:
            if data['name'].startswith('Vaal '):
                stats_string = '20/20 Price: {0:.0f}c\n'.format(data['chaosValue'])
            else:
                stats_string = '20q Price: {0:.0f}c\n'.format(data['chaosValue'])
    else:
        stats_string = ''
    if data['mana_multiplier']:
        stats_string+='Mana Multiplier: {}%\n'.format(data['mana_multiplier'])
    if data['radius']:
        stats_string+='Radius: {}\n'.format(data['radius'])
    if int(data['is_res']):
        stats_string+='Mana Reserved: {}%\n'.format(data['mana_cost'])
##        stats_string+='Mana Reserved: {}\n'.format(data['is_res'])
    elif data['mana_cost']:
        if data['mana_cost_max']:
            stats_string+='Mana Cost: ({}-{})\n'.format(data['mana_cost'],data['mana_cost_max'])
        else:
            stats_string+='Mana Cost: {}\n'.format(data['mana_cost'])
    if data['vaal_souls_requirement']:
        stats_string+='Souls Per Use: {}\n'.format(data['vaal_souls_requirement'])
    if data['vaal_stored_uses']:
        stats_string+='Can Store {} Use(s)\n'.format(data['vaal_stored_uses'])
    if data['stored_uses'] and int(data['stored_uses'])>1:
        stats_string+='Can Store {} Use(s)\n'.format(data['stored_uses'])
    if data['cooldown']:
        stats_string+='Cooldown Time: {}s\n'.format(data['cooldown'])    
    if data['cast_time'] and 'Attack' not in data['tags']:
        stats_string+='Cast Time: {}s\n'.format(data['cast_time'])
    if data['crit_chance']:
        stats_string+='Critical Strike Chance: {}%\n'.format(data['crit_chance'])
    if data['proj_speed']:
        stats_string+='Projectile Speed: {}\n'.format(data['proj_speed'])
    if data['attack_speed_multiplier'] and int(data['attack_speed_multiplier'])!=100:
        stats_string+='Attack Speed: {}% of base\n'.format(data['attack_speed_multiplier'])
    if data['damage_effectiveness']:
        if data['damage_effectiveness_max']:
            stats_string+='Damage Effectiveness: ({}-{})%\n'.format(data['damage_effectiveness'],data['damage_effectiveness_max'])
        elif int(data['damage_effectiveness'])!=100:
            stats_string+='Damage Effectiveness: {}%\n'.format(data['damage_effectiveness'])
    if data['level_requirement']:
        stats_string+='Requires Level: ({}-{})'.format(data['level_requirement'],data['level_requirement_max'])
        if data['str_requirement']:
            stats_string+=', ({}-{}) Str'.format(data['str_requirement'],data['str_requirement_max'])
        if data['dex_requirement']:
            stats_string+=', ({}-{}) Dex'.format(data['dex_requirement'],data['dex_requirement_max'])
        if data['int_requirement']:
            stats_string+=', ({}-{}) Int'.format(data['int_requirement'],data['int_requirement_max'])
        stats_string+='\n'
    if data['gem_desc']:
        stats_string+='{}\n\n'.format(data['gem_desc'])

    stats_string = bold_nums.sub(r'**\1**', stats_string.replace('<br>','\n')).replace('****','')
    if not stats_string:
        stats_string = '--'
    red = 0xc51e1e
    blue = 0x4163c9
    green = 0x08a842
    gemcolor = 0xffffff # white by default

    if data['primary_att'].lower() == 'strength':
        gemcolor = red
    if data['primary_att'].lower() == 'intelligence':
        gemcolor = blue
    if data['primary_att'].lower() == 'dexterity':
        gemcolor = green
        
    e = discord.Embed(url='https://pathofexile.gamepedia.com/{}'.format(data['name'].replace(' ','_')),
        title=data['name'],
        type='rich',color=gemcolor)
    e.add_field(name=data['tags'],value=_strip_html_tags(stats_string),inline=False)

    if 'icon' in data.keys() and data['icon']:
        e.set_thumbnail(url=data['icon'].replace(' ','%20'))
    if data['stat_text']:
        qual_bonus = None
        if quality==Quality.NORMAL and data['qual_bonus_normal']:
            qual_bonus = data['qual_bonus_normal']
        if quality==Quality.ANOMALOUS and data['qual_bonus_anomalous']:
            qual_bonus = data['qual_bonus_anomalous']
        if quality==Quality.DIVERGENT and data['qual_bonus_divergent']:
            qual_bonus = data['qual_bonus_divergent']
        if qual_bonus:
            e.add_field(name='Per 1% Quality:',value=bold_nums.sub(r'**\1**', '{}\n\n{}'.format(qual_bonus,data['stat_text']).replace('<br>','\n')).replace('****',''),inline=False)
        
    if not data['primary_att'].lower() == 'none':
        e.set_footer(text=data['primary_att'])
    else:
        e.set_footer(text='Colorless')
    return e

# will return a list of embeds for all "unread" announcements
# returns tuples of (embed, filterable text or None)
async def scrape_forum(section = 'https://www.pathofexile.com/forum/view-forum/news', table = 'forum_announcements', header = 'Forum - Announcements'):
    loop = asyncio.get_event_loop() # could also use bot.loop or whatever it is
    future = loop.run_in_executor(None, requests.get, section)
    data = await future
    etree = lxmlhtml.fromstring(data.text)
    titles = [a.strip() for a in etree.xpath('//div[@class="title"]/a/text()')]
    urls = [urlparse.urljoin('https://www.pathofexile.com/',a) for a in etree.xpath('//div[@class="title"]/a/@href')]
    threadnums = [a.split('/')[-1] for a in urls]
    threads = list(zip(titles,urls,threadnums))
    announces = []
    
    r=bot.cursor.execute('SELECT threadnum FROM `{}` WHERE threadnum IN ({})'.format(table, ','.join([x[2] for x in threads])))
    already_parsed = [x[0] for x in r.fetchall()]

    new_threads = filter(lambda x: x[2] not in already_parsed,threads)
    for thread in new_threads:
        embed_img = None
        # try to get a header image, literally (these only appear in forum announcements)
        try:
            if table == 'forum_announcements':
                loop = asyncio.get_event_loop()
                future = loop.run_in_executor(None, requests.get, thread[1])
                data = await future

                if data.status_code == 200:
                    etree = lxmlhtml.fromstring(data.text)
                    embed_img = etree.xpath('//tr[contains(@class,"newsPost")]//img/@src')[0]
        except:
            pass
        r=bot.cursor.execute('SELECT 1 FROM %s WHERE threadnum=?'%table,(thread[2],))
        if r.fetchone():
            break
        else:
            #announce.
            bot.cursor.execute('INSERT INTO %s (title,url,threadnum) VALUES (?,?,?)'%table,thread)
            bot.conn.commit()
            announces.append((_create_forum_embed(thread[1],thread[0],header,img=embed_img),None))
    return announces

# returns tuples of (embed, filterable text or None)
async def scrape_deals(deal_api = 'https://www.pathofexile.com/api/shop/microtransactions/specials?limit=9999'):
##    r=bot.cursor.execute('''select 1 from daily_deals where datetime(end_date)>datetime('now')''')
##    if r.fetchone(): #ongoing deal, no need to check for new ones.
##        return None
    loop = asyncio.get_event_loop() # could also use bot.loop or whatever it is
    future = loop.run_in_executor(None, requests.get, deal_api)
    data = await future
    data.raise_for_status()
    js = data.json()
    if js['total'] == 0:
        return []
    itemhash = hashlib.md5(json.dumps(js, sort_keys=True).encode('utf8')).hexdigest()
    r=bot.cursor.execute('SELECT 1 FROM daily_deals WHERE hash=?',(itemhash,))
    if r.fetchone():
        return []
    start_dates = set([i['startAt'] for i in js['entries']])
    latest_deal = sorted(start_dates)[-1]
    latest_deals = sorted([i for i in js['entries'] if i['startAt']==latest_deal], key=lambda x: x['priority'], reverse=True)
    
    img_url = latest_deals[0]['imageUrl']
    end_date = latest_deals[0]['endAt'] # end date isn't accurate as deals could have different end dates.
    latest_names = [x['microtransaction']['name'] for x in latest_deals]
    title = ' | '.join(latest_names[:2])
    if int(js['total']) >2:
        title += ' | + %i more'%(int(js['total'])-2)
    #announce.
    bot.cursor.execute('REPLACE INTO daily_deals (title,img_url,hash,end_date) VALUES (?,?,?,?)',(title,img_url,itemhash,end_date))
    bot.cursor.execute('''DELETE FROM daily_deals WHERE ROWID IN (SELECT ROWID FROM daily_deals ORDER BY ROWID DESC LIMIT -1 OFFSET 7)''')
    bot.conn.commit()
    return ((_create_deal_embed(title,img_url),'\n'.join(latest_names)),)

def _create_forum_embed(url,title,name='Forum Announcement',thumb_url='https://web.poecdn.com/image/favicon/ogimage.png?v=1',img=None):
    e = discord.Embed(url=url,
        title=title,
        type='rich')
    e.set_author(name=name,url=url.replace('view-thread','post-reply'))
    if img:
        e.set_image(url=img)
    else:
        e.set_thumbnail(url=thumb_url)
    return e

def _create_deal_embed(title,img_url,name='Daily Deal'):
    e = discord.Embed(url='https://www.pathofexile.com/shop/category/daily-deals',
        title=title,
        type='rich')
    e.set_thumbnail(url=img_url)
    e.set_author(name=name)
    return e

def _create_pin_embed(pin):
    content = pin.content
    thumb = None
    if pin.embeds:
        emb = pin.embeds[0]
        if emb.thumbnail != discord.Embed.Empty:
            thumb = emb.thumbnail.url
        if not content:
            if emb.title != discord.Embed.Empty:
                content = emb.title
            elif emb.description != discord.Embed.Empty:
                content = emb.description
    e = discord.Embed(
        description=content,
        type='rich',
        color=0x7289da,
        timestamp=pin.created_at
    )
    if thumb:
        e.set_thumbnail(url = thumb)
    if pin.attachments:
        e.set_image(url = pin.attachments[0].url)
    e.set_author(
        name = pin.author.display_name,
        icon_url = pin.author.avatar_url,
        url = 'https://discord.com/users/{}'.format(pin.author.id)
    )
    e.add_field(name='Original Message:',value='https://discord.com/channels/{}/{}/{}'.format(pin.guild.id,pin.channel.id,pin.id),inline=False)
    e.set_footer(text='#{}'.format(pin.channel.name))
    return e

class backgroundTasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminders.start()
        self.forum_announcements.start()

    def cog_unload(self):
        self.reminders.cancel()
        self.forum_announcements.cancel()
        
    @tasks.loop(seconds=5.0)
    async def reminders(self):
        r=bot.cursor.execute('''SELECT creator,role,channel,server,datetime,message FROM reminders WHERE datetime <= datetime('now')''')
        for row in r.fetchall():
            'announce and delete.'
            try:
                await bot.send_message(bot.get_channel(row[2]), '<@{}> {}'.format(row[0],row[5]),code_block=False)
            except:
                'channel missing or bot is blocked'
            finally:
                try:
                    bot.cursor.execute('DELETE FROM reminders WHERE creator = ? and role = ? and channel = ? and server = ? and datetime = ? and message = ?', row)
                    bot.conn.commit()
                except:
                    pass
            
    @tasks.loop(seconds=60.0)
    async def forum_announcements(self):
        announce_types = [('forumannounce',partial(scrape_forum)),
                          ('patchnotes',partial(scrape_forum,'https://www.pathofexile.com/forum/view-forum/patch-notes','patch_notes','Forum - Patch Notes')),
                           ('dailydeal',partial(scrape_deals))]
        for name,func in announce_types:
            try:
                data = await func()
                if data:
                    # get filters from this channel if found.
                    # compare to filterable string which func() should return. meaning we need to modify func.
                    # only send embed if filter matches.
                    r=bot.cursor.execute('SELECT channel FROM announce WHERE type=?',(name,))
                    for channel in [i[0] for i in r.fetchall()]:
                        try:
                            for e,filterstr in data:
                                r2=bot.cursor.execute('SELECT regexp FROM regexp_filters WHERE type=? AND channel=?',(name,channel))
                                regex = r2.fetchone()
                                if regex and filterstr:
                                    if not re.search(regex[0],filterstr,flags=re.I|re.M):
                                        continue
                                await bot.send_message(bot.get_channel(channel), embed=e)
                        except:
                            'channel missing or bot is blocked'
                            # raise
            except Exception as e:
                print('error scraping forums (%s): %r'%(name,e))
##                raise
                'just for extra safety because an error here means the loop stops'
                'this can be caused by things like maintenance'

    @reminders.before_loop
    @forum_announcements.before_loop
    async def before_run(self):
        await self.bot.wait_until_ready()
        
if __name__ =='__main__':
    bot.db = db.PoeDB(ro=True)
    bot.conn = sqlite3.connect('announce.sqlitedb')
    bot.cursor=bot.conn.cursor()
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS announce
             (channel int,
             type text,
             PRIMARY KEY (channel,type))''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS forum_announcements
             (title text,
             url text,
             threadnum text PRIMARY KEY)''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS patch_notes
             (title text,
             url text,
             threadnum text PRIMARY KEY)''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS daily_deals
             (title text,
             img_url text,
             hash text PRIMARY KEY)''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS daily_labs
             (date text,
             diff text,
             img_url text,
             PRIMARY KEY (date,diff))''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS regexp_filters
             (channel int,
             type text,
             regexp text,
             PRIMARY KEY (channel,type))''')
    try:
        bot.cursor.execute('''ALTER TABLE daily_deals ADD COLUMN end_date real''')
    except sqlite3.OperationalError:
        pass
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS pins
             (source int PRIMARY KEY,
             dest int)''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS pricecheck
             (channel int PRIMARY KEY,
             league text)''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS reminders
             (creator int,
             role int DEFAULT 0,
             channel int DEFAULT 0,
             server int DEFAULT 0,
             datetime real,
             message text,
             interval int DEFAULT 0,
             PRIMARY KEY (creator,server,message,datetime,channel,role))''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS timezones
             (server int PRIMARY KEY,
             timezone text DEFAULT "UTC")''')
    bot.conn.commit()
    import cogs
    from cogs import *
    cogs.setup_all_cogs(bot)
    bot.add_cog(Alerts())
    bot.add_cog(Info())
    bot.add_cog(backgroundTasks(bot))
    with open('token','r') as f:
        bot.run(f.read())

##https://discord.com/oauth2/authorize?client_id=313788924151726082&scope=bot&permissions=0
