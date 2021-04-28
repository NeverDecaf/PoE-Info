from discord.ext import commands, tasks
import discord
from dateparser import parse
from dateparser.utils import localize_timezone
import datetime
import re
import sys
from pytz import UnknownTimeZoneError
sys.path.append("..") # Adds higher directory to python modules path.
from bot import admin_or_dm,PRIVATE_CHANNEL
DISCORD_PIN_LIMIT = 50
def parse_longest_substr_time(txt: str, settings: dict):
    dt, msg = None, txt
    tokens = re.split('(\s)', txt)
    if len(tokens)<3:
        return dt,msg # input is too short
    for i in range(1,len(tokens),2):
        substr = ''.join(tokens[:i])
        tmpdt = parse(substr, settings = settings)
        if not tmpdt:
            continue
        if tmpdt < datetime.datetime.utcnow():
            diff = datetime.datetime.utcnow() - tmpdt
            tmpdt = datetime.datetime.utcnow() + diff
        dt = tmpdt
        msg = ''.join(tokens[i+1:])
    return dt, msg

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminders.start()
        
    def cog_unload(self):
        self.reminders.cancel()

    @tasks.loop(seconds=5.0)
    async def reminders(self):
        r=self.bot.cursor.execute('''SELECT creator,role,channel,server,datetime,message FROM reminders WHERE datetime <= datetime('now')''')
        for row in r.fetchall():
            'announce and delete.'
            try:
                await self.bot.send_message(self.bot.get_channel(row[2]), '<@{}> {}'.format(row[0],row[5]),code_block=False)
            except:
                'channel missing or bot is blocked'
            finally:
                try:
                    self.bot.cursor.execute('DELETE FROM reminders WHERE creator = ? and role = ? and channel = ? and server = ? and datetime = ? and message = ?', row)
                    self.bot.conn.commit()
                except:
                    pass

    @reminders.before_loop
    async def before_run(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_channel_pins_update(self, chan, last_pin):
        try:
            pins = await self.bot.pins_from(chan)
        except (discord.errors.NotFound, discord.errors.Forbidden):
            'missing permissions'
            return
        if len(pins) >= DISCORD_PIN_LIMIT:
            r=self.bot.cursor.execute('SELECT dest FROM pins WHERE source=?',(chan.id,))
            dest = r.fetchone()
            if dest:
                pin_channel=self.bot.get_channel(dest[0])
                if pin_channel and self._pin_perm_check(chan.guild, chan, pin_channel):
                    await self._move_pins(pins[-1:], pin_channel)

    @commands.group(pass_context=True, invoke_without_command=True)
    @admin_or_dm()
    @commands.guild_only()
    async def pin(self, ctx, count : str):
        '''<count>|set <channel>
        Move <count> pins to the linked pin channel
        use -pin set <channel> to link this channel to a destination channel for pins.'''
        try:
            assert int(count)>0
        except:
            raise commands.BadArgument()
        r=self.bot.cursor.execute('SELECT dest FROM pins WHERE source=?',(ctx.message.channel.id,))
        dest = r.fetchone()
        if not dest:
            await self.bot.send_message(ctx.message.channel, 'No destination channel set for pins, use -pin set <channel>')
            return
        pin_channel=self.bot.get_channel(dest[0])
        # do a extra permissions check for safety:
        if pin_channel and self._pin_perm_check(ctx.message.guild,ctx.message.channel,pin_channel):
            pins = await self.bot.pins_from(ctx.message.channel)
            await self._move_pins(list(reversed(pins))[:min(len(pins),int(count[0]))],pin_channel)
        else:
            await self.bot.send_message(ctx.message.channel, 'Invalid pin channel (must be a channel on this server + bot must have proper permissions)')
            return
            
    @pin.command(name='set', pass_context=True)
    @admin_or_dm()
    @commands.guild_only()
    async def pins_set(self, ctx, channel: str):
        '''<channel>
        Set the pin channel for this channel.
        Pins from this channel will be moved automatically when the pin list is full or manually by using -pin <count>'''
        try:
            just_id = channel[2:-1]
            ch = self.bot.get_channel(int(just_id))
            if ch and self._pin_perm_check(ctx.message.guild,ctx.message.channel,ch):
                self.bot.cursor.execute('REPLACE INTO pins(source,dest) VALUES(?,?)',(ctx.message.channel.id,just_id))
                self.bot.conn.commit()
                await self.bot.send_message(ctx.message.channel, 'Set pin destination for {} to {}'.format(ctx.message.channel.mention,ch.mention),code_block=False)
                return
            else:
                raise Exception()
        except:
            await self.bot.send_message(ctx.message.channel, 'Invalid pin channel (must be a channel on this server + bot must have proper permissions)')
            return
            
    def _pin_perm_check(self,server,src,dst):
        src_perms=src.permissions_for(server.me)
        dest_perms=dst.permissions_for(server.me)
        return dest_perms.send_messages and dest_perms.attach_files and dest_perms.embed_links\
               and src_perms.read_message_history and src_perms.manage_messages and src_perms.read_messages
               
    async def _move_pins(self,pinlist,pin_channel):
        for pin in pinlist:
            e = self._create_pin_embed(pin)
            await self.bot.send_message(pin_channel, code_block=False, embed = e)
            await self.bot.unpin_message(pin) # This isnt working, or pins_from isnt refreshsed
    
    def _is_valid_tz(self, tzstr):
        try:
            localize_timezone(datetime.datetime.now(),tzstr)
            return True
        except UnknownTimeZoneError:
            return False
    @commands.command(pass_context=True, aliases = ['remind','remindme'], invoke_without_command=True)
    async def reminder(self, ctx, *query: str):
        '''<datetime/timedelta> <message>
    Set a reminder; for example:
    -reminder 3pm June 10 hello
    -reminder 3 days 10 hours world
    sub-commands:
    reminder list - list all reminders for yourself
    reminder delete <index> - delete specified reminder
    reminder timezone <tz> - set timezone for date reminders'''
        if not query:
            raise commands.BadArgument()
        helpmsg = 'usage:\n-reminder <datetime/timedelta> <message>'
        time_display_format = "%Y-%m-%d %H:%M:%S"
        isprivate = ctx.message.channel.type == PRIVATE_CHANNEL
        fulltext = ' '.join(query)
        if not len(query):
            await self.bot.send_message(ctx.message.channel, helpmsg)
            return
        server_id = ctx.message.guild and ctx.message.guild.id or ctx.message.channel.id
        subcmd = query[0]
        r = self.bot.cursor.execute('SELECT timezone from timezones where server=?',(server_id,)).fetchone()
        settings = {'TIMEZONE':(r and r[0]) or 'UTC', 'TO_TIMEZONE':'UTC', 'PREFER_DATES_FROM': 'future'}
        disp_settings = {'TO_TIMEZONE':(r and r[0]) or 'UTC', 'TIMEZONE':'UTC', 'PREFER_DATES_FROM': 'future'}
        if subcmd in ('list','-l'):
            r = self.bot.cursor.execute('SELECT message,datetime FROM reminders where creator = ? and server = ? ORDER by datetime ASC',(ctx.message.author.id,server_id))
            res = r.fetchall()
            if not res:
                await self.bot.send_message(ctx.message.channel, 'You have 0 reminders.')
                return
            p = ''
            for i,r in enumerate(res):
                p+= '{}. "{}" on {}\n'.format(i,r[0],parse(r[1],settings = disp_settings).strftime(time_display_format))
            await self.bot.send_message(ctx.message.channel, p)
        elif subcmd in ('delete','del'):
            if len(query)<2 or not re.match('^\d*$',query[1]):
                await self.bot.send_message(ctx.message.channel, 'usage:\n-reminder del <index>')
                return
            r = self.bot.cursor.execute('SELECT creator,role,channel,server,datetime,message FROM reminders where creator = ? and server = ? ORDER by datetime ASC',(ctx.message.author.id,server_id))
            res = r.fetchall()
            self.bot.cursor.execute('DELETE FROM reminders WHERE creator = ? and role = ? and channel = ? and server = ? and datetime = ? and message = ?', res[int(query[1])])
            self.bot.conn.commit()
            await self.bot.send_message(ctx.message.channel, 'Reminder deleted.')
        elif subcmd in ('timezone','tz'):
            if len(query)<2:
                await self.bot.send_message(ctx.message.channel, 'usage:\n-reminder timezone <timezone>')
                return
            if not isprivate and not ctx.message.author.permissions_in(ctx.message.channel).administrator:
                await self.bot.send_message(ctx.message.channel, 'You must be an administrator to set reminder timezone for this server.')
                return
            tz = query[1]
            if not self._is_valid_tz(tz):
                await self.bot.send_message(ctx.message.channel, 'Invalid timezone.')
                return
            self.bot.cursor.execute('REPLACE INTO timezones(server,timezone) VALUES(?,?)',(server_id,tz))
            self.bot.conn.commit()
            await self.bot.send_message(ctx.message.channel, 'Server timezone set to "{}"'.format(tz))
        # elif subcmd is role or channel, this isnt that useful, maybe implement later.
        # elif re.match('^@&.*$',subcmd):
            # if not ctx.message.author.permissions_in(ctx.message.channel).administrator:
                # await self.bot.send_message(ctx.message.channel, 'You must be an administrator to set reminders for a role')
                # return
            # print([role.name for role in ctx.message.guild.roles],subcmd[1:])
            # validroles = [role for role in ctx.message.guild.roles if role.name==subcmd[1:]]
            # if not validroles:
                # await self.bot.send_message(ctx.message.channel, 'Role not found.',code_block=False)
                # return
            # await self.bot.send_message(ctx.message.channel, 'channel, @{}'.format(validroles[0]),code_block=False)
        elif len(query)>1:
            date,msg = parse_longest_substr_time(fulltext, settings)
            if not date:
                await self.bot.send_message(ctx.message.channel, 'Could not find a time or date in your message, try being more specific. For example, use "in 10 days" instead of "10 days" or "5 minutes" instead of "5m".')
                return
            if date.tzinfo:
                await self.bot.send_message(ctx.message.channel, 'timezone argument not (currently) supported, set global timezone for this server with -reminder timezone <tz>')
                return
            if date <= datetime.datetime.utcnow():
                await self.bot.send_message(ctx.message.channel, 'Given date ({}) has already passed, try being more specific.'.format(date))
                return
            self.bot.cursor.execute('REPLACE INTO reminders(creator,server,channel,datetime,message) VALUES(?,?,?,?,?)',(ctx.message.author.id,server_id,ctx.message.channel.id,date,msg))
            self.bot.conn.commit()
            await self.bot.send_message(ctx.message.channel, 'reminder set for {}'.format(parse(date.strftime("%Y-%m-%d %H:%M:%S.%f"),settings = disp_settings).strftime(time_display_format)))
        else:
            await self.bot.send_message(ctx.message.channel, helpmsg)
        return
            
    def _create_pin_embed(self, pin):
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

def setup(bot):
    bot.cursor.execute('''CREATE TABLE IF NOT EXISTS pins
             (source int PRIMARY KEY,
             dest int)''')
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
    bot.add_cog(Utility(bot))