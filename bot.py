#!/usr/bin/env python3
import discord
from discord.ext import commands
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
abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)
MESSAGE_EDITABLE_TIMEOUT = 60*10 # seconds, max of 1 day.
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
class BotWithReactions(commands.Bot):
    DELETE_EMOJI = '\U0000274C'
    REACTION_TIMEOUT = 60*60*12 # seconds (was 300)
    REACTIONBUTTONS={}
    CLEANUP_TIMEOUT = 60 # seconds
    AUTO_CLEANUP = OrderedDict()
    CLEANUP_KEY = 0
    DEFAULT_FAILURE_MSG = '```No Results.```'
    async def send_failure_message(self,destination,failure_message=DEFAULT_FAILURE_MSG,message=None,**kwargs):
        ''' message is the user message the bot is replying to. if provided we can autodelete failure messages if the original is edited. '''
        sent_msg = await super().send_message(destination,content=failure_message, **kwargs)
        if not destination.type == PRIVATE_CHANNEL:
            self.AUTO_CLEANUP[message or self.CLEANUP_KEY] = (time.time(),sent_msg)
            self.CLEANUP_KEY = (self.CLEANUP_KEY+1)%1000000
        return sent_msg
    async def send_file(self, destination, fp, failure_message=DEFAULT_FAILURE_MSG, **kwargs):
        if fp:
            # we don't add ``` around the content here as it looks too bulky.
            sent_msg = await super().send_file(destination, fp, **kwargs)
        else:
            sent_msg = await self.send_failure_message(destination,failure_message = failure_message)
        return sent_msg
    async def send_message(self, destination, content=None, failure_message=DEFAULT_FAILURE_MSG, code_block=True, **kwargs):
        if content and code_block:
            content = '```'+content.strip('`').rstrip('`')+'```' # turn our message into a code block.
        if content or kwargs.get('embed'): # if message is blank and no embed, send failure message instead
            sent_msg = await super().send_message(destination,content=content, **kwargs)
        else:
            sent_msg = await self.send_failure_message(destination,failure_message = failure_message)
        return sent_msg
    async def send_deletable_file(self,author,*args,**kwargs):
        '''
        attaches a X reaction that allows the requester (author) to delete the sent file

        only works in public channels. in PMs the message will be sent as normal.
        '''
        sent_msg = await self.send_file(*args, **kwargs)
        if isinstance(args[0],discord.channel.Channel) and not sent_msg.content == self.DEFAULT_FAILURE_MSG:
            await self.attach_button(sent_msg,author,self.DELETE_EMOJI,lambda x,y:self.delete_message(x))
        return sent_msg
    async def send_deletable_message(self,author,*args, code_block = True, **kwargs):
        '''
        attaches a X reaction that allows the requester (author) to delete the sent message

        only works in public channels. in PMs the message will be sent as normal.
        '''
        sent_msg = await self.send_message(*args, code_block=code_block, **kwargs)
        if isinstance(args[0],discord.channel.Channel) and not sent_msg.content == self.DEFAULT_FAILURE_MSG:
            await self.attach_button(sent_msg,author,self.DELETE_EMOJI,lambda x,y:self.delete_message(x))
        return sent_msg
    async def attach_button(self, message, author, emoji, callback, *data, user_restricted=True):
        '''Add a reaction button. When pressed callback will be called with message,author,*data as arguments.'''
        try:
            await self.add_reaction(message,emoji)
            if not user_restricted:
                author = None
            self.REACTIONBUTTONS[(message.id,author,emoji)]=(time.time(),callback,message,*data)
        except discord.errors.NotFound:
            pass # this one means the message/reaction was deleted already so no big deal just ignore
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
            
    async def process_reactions(self,key,new_author=None):
        '''call this in on_reaction_add. For non-restricted buttons new_author must be passed (this will be the user allowed to delete the new message)
           After a reaction is pressed the button/reaction will be removed.'''
        if key in self.REACTIONBUTTONS:
            emoji = key[2]
            author = key[1]
            _,callback,msg,*data=self.REACTIONBUTTONS[key]
            if new_author:
                await callback(msg,new_author,*data)
            else:
                await callback(msg,author,*data)
            del self.REACTIONBUTTONS[key]
            try:
                await self.remove_reaction(msg,emoji,self.user)
            except discord.errors.NotFound:
                pass # this one means the message/reaction was deleted already so no big deal just ignore
    async def remove_stale_reactions(self):
        '''Run this every ~1 second in a background loop. This simply removes reactions that have expired. (set REACTION_TIMEOUT)'''
        now = time.time()
        for key in list(self.REACTIONBUTTONS.keys()):
            emoji = key[2]
            msg_timestamp,_,msg,*_=self.REACTIONBUTTONS[key]
            if now-msg_timestamp>self.REACTION_TIMEOUT:
                del self.REACTIONBUTTONS[key]
                try:
                    await self.remove_reaction(msg,emoji,self.user)
                except discord.errors.NotFound:
                    pass # this one means the message/reaction was deleted already so no big deal just ignore

                
bot = BotWithReactions(command_prefix='-', description='PoE Info.')

@bot.event
async def on_reaction_add(reaction,user):
    if reaction.me and user!=bot.user and user is not None:
        await bot.process_reactions((reaction.message.id,user,reaction.emoji))
        await bot.process_reactions((reaction.message.id,None,reaction.emoji),new_author=user)
@bot.event
async def on_message_edit(before,after):
    datediff = (datetime.datetime.utcnow() - before.timestamp)
    if before.content!=after.content and datediff.days<1 and datediff.seconds<MESSAGE_EDITABLE_TIMEOUT: # need this check because auto-embed counts as editing
        await bot.process_commands(after)
        try:
            await bot.edited_cleanup(after) # this can error due to race condition
        except:
            pass
async def cleanup_reactions():
    await bot.wait_until_ready()
    await bot.wait_until_login() # just in case .is_closed is true before login.
    while not bot.is_closed:
        try:
            events = bot.db.upcoming_event()
            nextevent = bot.db.event_ending()
            if events or nextevent:
                r=bot.cursor.execute('SELECT channel FROM announce WHERE type="event"')
                for channel in [i[0] for i in r.fetchall()]:
                    try:
                        if events:
                            for event in events:
                                await bot.send_message(discord.Object(id=channel), '%s'%str(event[0]))
                        if nextevent:
                            await bot.send_message(discord.Object(id=channel), 'diff\n%s'%nextevent)
                    except:
##                        raise
                        'channel missing or bot is blocked'
            await bot.remove_stale_reactions()
            await bot.auto_cleanup()
        except:
##            raise
            'just for extra safety because an error here means the loop stops'
        await asyncio.sleep(10)

async def forum_announcements():
    await bot.wait_until_ready()
    await bot.wait_until_login() # just in case .is_closed is true before login.
    while not bot.is_closed:
        announce_types = [('forumannounce',partial(scrape_forum)),
                          ('patchnotes',partial(scrape_forum,'https://www.pathofexile.com/forum/view-forum/patch-notes','patch_notes','Patch Notes')),
                           ('dailydeal',partial(scrape_deals))]
        for name,func in announce_types:
            try:
                data = await func()
                if data:
                    r=bot.cursor.execute('SELECT channel FROM announce WHERE type=?',(name,))
                    for channel in [i[0] for i in r.fetchall()]:
                        try:
                            for e in data:
                                await bot.send_message(discord.Object(id=channel), embed=e)
                        except:
                            'channel missing or bot is blocked'
            except Exception as e:
                print('error scraping forums (%s): %r'%(name,e))
                raise
                'just for extra safety because an error here means the loop stops'
                'this can be caused by things like maintenance'
        await asyncio.sleep(60)

@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')
    await bot.change_presence(game=discord.Game(name='-help'))
    
@bot.command(pass_context=True)
async def pin(ctx, *count : str):
    '''<count>|<set>
Number of pins to move OR set a channel for pins.'''
##    for chan in ctx.message.server.channels:
    def perm_check(src,dst):
        src_perms=src.permissions_for(ctx.message.server.me)
        dest_perms=dst.permissions_for(ctx.message.server.me)
        return dest_perms.send_messages and dest_perms.attach_files and dest_perms.embed_links\
               and src_perms.read_message_history and src_perms.manage_messages and src_perms.read_messages

    if len(count)>1 and count[0] == 'set':
        # set pin channel
        if not ctx.message.author.permissions_in(ctx.message.channel).administrator:
            await bot.send_message(destination, 'You must be an administrator to set pin channel.')
            return
        try:
            just_id = count[1][2:-1]
            ch = bot.get_channel(just_id)
            if ch and perm_check(ctx.message.channel,ch):
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
    pin_channel=bot.get_channel(str(dest[0]))
    # do a extra permissions check for safety:
    if pin_channel and perm_check(ctx.message.channel,pin_channel):
        pins = await bot.pins_from(ctx.message.channel)
        for pin in list(reversed(pins))[:min(len(pins),int(count[0]))]:
            msg_content = '{} ({}): {}'.format(pin.author.nick or pin.author,pin.edited_timestamp.strftime("%m/%d/%y") if pin.edited_timestamp else pin.timestamp.strftime("%d/%m/%y"),pin.content)
            if pin.attachments:
                buffer = io.BytesIO()
                r = requests.get(pin.attachments[0]['url'], stream=True)
                shutil.copyfileobj(r.raw, buffer)
                buffer.seek(0)
                await bot.send_file(pin_channel,buffer,filename=pin.attachments[0]['filename'],content=msg_content)
            else:
                await bot.send_message(pin_channel, msg_content,code_block=False)
            await bot.unpin_message(pin) # This isnt working, or pins_from isnt refreshsed
    else:
        await bot.send_message(ctx.message.channel, 'Invalid pin channel (must be a channel on this server + bot must have proper permissions)')
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

@bot.command(pass_context=True,aliases=['setleague','pc','league','pricecheck'])
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
class Alerts:
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
        '''<on|off>
    Turn daily deal announcements on/off.'''
        await announce_internals(ctx,' '.join(toggle),'dailydeal','Daily deal announcements','deals')
        
    @commands.command(pass_context=True)
    async def events(self, ctx, *toggle : str):
        '''<on|off>
    Turn event announcements on/off.'''
        await announce_internals(ctx,' '.join(toggle),'event','Event announcements','events')
class Info:
    'Show info on in-game items. These commands have one letter aliases for quicker use (ex: -u)'
    @commands.command(pass_context=True,aliases=['u'])
    async def unique(self, ctx, *itemname: str):
        '''<item>
    Shows stats for an item. Partial names acceptable.'''
        if not len(itemname):
            await bot.send_message(ctx.message.channel, 'usage: -u <item name>')
            return
        # consider showing flavor text in the embed footer
        item = ' '.join(itemname)
        r=bot.cursor.execute('SELECT league FROM pricecheck WHERE channel=?',(ctx.message.channel.id,))
        league = (r.fetchone() or ('tmpStandard',))[0]
        data = bot.db.get_data('unique_items',item,league)
        if not data:
            await bot.send_failure_message(ctx.message.channel)
            return
        if len(data)>1:
            #send choices
            sent_msg= await bot.send_message(ctx.message.channel, 'Multiple Results:\n'+'\n'.join(['%i. %s'%(i+1,datum['name']) for i,datum in enumerate(data)]))
            for i in range(min(SEARCH_REACTION_LIMIT,len(data))):
                await bot.attach_button(sent_msg, ctx.message.author, DIGIT_EMOJI[i], _search_result, data[i])#, _search_result, data[i][3])
            return
        e = _create_unique_embed(data[0])
        await bot.send_deletable_message(ctx.message.author, ctx.message.channel, embed=e)
        
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
                await bot.attach_button(sent_msg, ctx.message.author, DIGIT_EMOJI[i], _gem_search_result, data[i])#, _search_result, data[i][3])
            return
        e = _create_gem_embed(data[0])
        await bot.send_deletable_message(ctx.message.author, ctx.message.channel, embed=e)

async def _search_result(msg, author, data):
    e = _create_unique_embed(data)
    await bot.send_deletable_message(author, msg.channel, embed=e)
    await bot.delete_message(msg)

async def _gem_search_result(msg, author, data):
    e = _create_gem_embed(data)
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
        description=stats_string,
        title='\n'.join((data['name'].strip(),data['baseitem'].strip())),
        type='rich',color=0xaf6025)
    if 'icon' in data.keys() and data['icon']:
        e.set_thumbnail(url=data['icon'].replace(' ','%20'))
    if data['impl'] or data['expl']: #this is only for tabula
        e.add_field(name=bold_nums.sub(r'**\1**', str(data['impl'])).replace('****','') if data['impl'] else '--',value=bold_nums.sub(r'**\1**', str(data['expl'])).replace('****','') if data['expl'] else '--',inline=False)
    if data['physdps'] or data['eledps']:
        s=''
        if data['physdps']:
            s+="Physical DPS: {} ".format(data['physdps'])
        if data['eledps']:
            s+="Elemental DPS: {}".format(data['eledps'])
        e.set_footer(text=s)
    return e

def _create_gem_embed(data):
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
        stats_string+='Can store {} use(s)\n'.format(data['vaal_stored_uses'])
    if data['cooldown']:
        stats_string+='Cooldown Time: {}s\n'.format(data['cooldown'])    
    if data['cast_time']:
        stats_string+='Cast Time: {}s\n'.format(data['cast_time'])
    if data['crit_chance']:
        stats_string+='Critical Strike Chance: {}%\n'.format(data['crit_chance'])
    if data['proj_speed']:
        stats_string+='Projectile Speed: {}\n'.format(data['proj_speed'])
    if data['damage_effectiveness_max'] and data['damage_effectiveness']:
        stats_string+='Damage Effectiveness: ({}-{})%\n'.format(data['damage_effectiveness'],data['damage_effectiveness_max'])
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
    e.add_field(name=data['tags'],value=stats_string,inline=False)

    if 'icon' in data.keys() and data['icon']:
        e.set_thumbnail(url=data['icon'].replace(' ','%20'))
        
    if data['qual_bonus'] and data['stat_text']:
        e.add_field(name='Per 1% Quality:',value=bold_nums.sub(r'**\1**', '{}\n\n{}'.format(data['qual_bonus'],data['stat_text']).replace('<br>','\n')).replace('****',''),inline=False)
        
    if not data['primary_att'].lower() == 'none':
        e.set_footer(text=data['primary_att'])
    else:
        e.set_footer(text='Colorless')
    return e

# will return a list of embeds for all "unread" announcements
async def scrape_forum(section = 'https://www.pathofexile.com/forum/view-forum/news', table = 'forum_announcements', header = 'Forum Announcement'):
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
        r=bot.cursor.execute('SELECT 1 FROM %s WHERE threadnum=?'%table,(thread[2],))
        if r.fetchone():
            break
        else:
            #announce.
            bot.cursor.execute('INSERT INTO %s (title,url,threadnum) VALUES (?,?,?)'%table,thread)
            bot.conn.commit()
            announces.append(_create_forum_embed(thread[1],thread[0],header))
    return announces

async def scrape_deals(deals = 'https://www.pathofexile.com/shop/category/daily-deals'):
    loop = asyncio.get_event_loop() # could also use bot.loop or whatever it is
    future = loop.run_in_executor(None, requests.get, deals)
    data = await future
    etree = lxmlhtml.fromstring(data.text)
    itemnames = [a.strip() for a in etree.xpath('//a[contains(@class,"itemImage")]/@alt')]
    im_urls = etree.xpath('//a[contains(@class,"itemImage")]/@data-href')
    import hashlib
    now = datetime.datetime.now()
    itemhash = hashlib.md5(str(itemnames).encode('utf8')).digest()
    title = ' | '.join(itemnames[:2])
    if len(itemnames) >2:
        title += ' | + %i more'%(len(itemnames)-2)
    r=bot.cursor.execute('SELECT 1 FROM daily_deals WHERE hash=?',(itemhash,))
    if r.fetchone() or not im_urls:
        return None
    else:
        #announce.
        bot.cursor.execute('REPLACE INTO daily_deals (title,img_url,hash) VALUES (?,?,?)',(title,im_urls[0],itemhash))
        bot.cursor.execute('''DELETE FROM daily_deals WHERE ROWID IN (SELECT ROWID FROM daily_deals ORDER BY ROWID DESC LIMIT -1 OFFSET 7)''')
        bot.conn.commit()
        return (_create_deal_embed(title,im_urls[0]),)

def _create_forum_embed(url,title,name='Forum Announcement'):
    e = discord.Embed(url=url,
        title=title,
        type='rich')
    e.set_author(name=name,url=url.replace('view-thread','post-reply'))
    return e

def _create_deal_embed(title,img_url,name='Daily Deal'):
    e = discord.Embed(url='https://www.pathofexile.com/shop/category/daily-deals',
        title=title,
        type='rich')
    e.set_thumbnail(url=img_url)
    e.set_author(name=name)
    return e

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
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS pins
             (source int PRIMARY KEY,
             dest int)''')
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS pricecheck
             (channel int PRIMARY KEY,
             league text)''')
    bot.conn.commit()

    bot.add_cog(Alerts())
    bot.add_cog(Info())
    with open('token','r') as f:
        # if any (background) task raises an exception, end this bot.
        tasks = [bot.start(f.read()),bot.loop.create_task(cleanup_reactions()),bot.loop.create_task(forum_announcements())]
        bot.loop.run_until_complete(asyncio.gather(*tasks))

##https://discordapp.com/oauth2/authorize?client_id=313788924151726082&scope=bot&permissions=0
