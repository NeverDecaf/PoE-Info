from discord.ext import commands,tasks
from discord import Embed
from urllib.parse import urlparse,urlunparse,parse_qsl,urlencode
import requests
import sqlite3
import asyncio
RESIN_CAP = 160
RESIN_REGEN_IN_MINUTES = 8
SMALLEST_SPENDABLE_RESIN = 40 # used for -resin reset

class GenshinTools(commands.Cog, name='Genshin Tools'):

    def __init__(self, bot):
        self.bot = bot
        self.conn = sqlite3.connect('resin.sqlitedb')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS resin
             (user_id int,
             amount int,
             timestamp real,
             PRIMARY KEY (user_id))''') 
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS pity_rate_limit
             (user_id int,
             last_request int,
             PRIMARY KEY (user_id))''')
        self.resinalert.start()
        
    def cog_unload(self):
        self.resinalert.cancel()
        
    @commands.command(hidden=True)
    async def resin(self, ctx, amount=None):
        ''' [<current amount>|reset] 
        Can also provide a negative amount to modify or use -resin reset to remove largest multiple of 40 (will leave <40 remaining).'''
        if amount:
            try:
                assert int(amount)<=RESIN_CAP
                if int(amount)<0:
                    self.cursor.execute(''' UPDATE resin set amount=amount+? where user_id=?''',(int(amount),ctx.author.id,))
                else:
                    self.cursor.execute(''' REPLACE INTO resin (user_id,amount,timestamp)  VALUES (?,?,julianday('now')) ''',(ctx.author.id,int(amount)))
                self.conn.commit()
            except:
                if amount.lower() == 'reset':
                    self.cursor.execute('''update resin set amount=amount-cast(((julianday('now')-timestamp)/(?/(24.*60)) + amount) / ? as int) * ? where user_id=?''',(RESIN_REGEN_IN_MINUTES,SMALLEST_SPENDABLE_RESIN,SMALLEST_SPENDABLE_RESIN,ctx.author.id,))
                    self.conn.commit()
                else:
                    await self.bot.send_message(ctx.message.channel,'Invalid resin amount.')
                    return
        self.cursor.execute(''' SELECT round((julianday('now')-timestamp)/(?/(24.*60))-.5) + amount from resin WHERE user_id=?''',(RESIN_REGEN_IN_MINUTES,ctx.author.id,))
        res = self.cursor.fetchone()
        if not res:
            await self.bot.send_message(ctx.message.channel,'Set resin amount first with -resin <amount>')
            return
        current_resin = min(RESIN_CAP,res[0])
        minutes_till_full = (RESIN_CAP - current_resin) * RESIN_REGEN_IN_MINUTES
        await self.bot.send_message(ctx.message.channel,'Current Resin: {:0.0f}/{}; {:.0f}:{:02.0f} until full'.format(current_resin,RESIN_CAP,minutes_till_full//60,minutes_till_full%60))
        
    async def getPityEmbed(self, feedback_url, banner = 'character'):
        banner_types = {
            'beginner': 100,
            'standard': 200,
            'character': 301,
            'weapon': 302,
        }
        parsedUrl = urlparse(feedback_url)
        qs = dict(parse_qsl(parsedUrl.query))
        qs.update({
        'auth_appid': 'webview_gacha',
        'init_type': '301',
        'gacha_id': 'b8fd0d8a6c940c7a16a486367de5f6d2232f53',
        'lang': 'en',
        'device_type': 'pc',
        'gacha_type': banner_types[banner],
        'size': 20})
        parsedUrl = parsedUrl._replace(path='event/gacha_info/api/getGachaLog')
        parsedUrl = parsedUrl._replace(netloc='hk4e-api-os.mihoyo.com')
        parsedUrl = parsedUrl._replace(fragment='')
        current_page = 1
        last_id = 0
        wishcount = 0
        pity4,pity5,name4,name5 = None,None,'Nothing','Nothing'
        wishlist = ['foobar']
        loop = asyncio.get_event_loop()
        while wishlist and (pity4==None or pity5==None) and wishcount < 90:
            qs.update({'end_id': last_id, 'page': current_page})
            r = await loop.run_in_executor(None, requests.get, urlunparse(parsedUrl._replace(query=urlencode(qs))))
            if r.status_code == 200:
                js = r.json()
                if js['retcode'] == 0:
                    wishlist = js['data']['list']
                    for wish in wishlist:
                        if pity4 == None and int(wish['rank_type']) == 4:
                            pity4 = wishcount
                            name4 = wish['name']
                        if pity5 == None and int(wish['rank_type']) == 5:
                            pity5 = wishcount
                            name5 = wish['name']
                        wishcount += 1
                else:
                    return None
            else:
                return None
            current_page += 1
            if not wishlist:
                break
            last_id = wishlist[-1]['id']
            await asyncio.sleep(1)
        if pity4 == None:
            pity4 = wishcount
        if pity5 == None:
            pity5 = wishcount
        e = Embed(title = 'Pity Counter')
        e.add_field(name = f'Last 4*: {name4}', value = f'{10 - pity4} Pull{"" if pity4==9 else "s"} until next pity ({pity4} pull{"" if pity4==1 else "s"} in)')
        e.add_field(name = f'Last 5*: {name5}', value = f'{90 - pity5} Pull{"" if pity5==89 else "s"} until next pity ({pity5} pull{"" if pity5==1 else "s"} in)')
        return e
        
    @commands.command(hidden=True)
    async def pity(self, ctx, banner, feedback_url):
        '''<banner> <feedback_url>
        <banner> must be one of: "character", "weapon", "standard"
        <feedback_url>: press esc and click "Feedback" in Paimon's menu, then copy the url that opens in your browser.
        
        *DO NOT USE THIS COMMAND IN PUBLIC CHANNELS*
        Your feedback url gives others access to your account (for up to 24h), only use this in DMs to be safe.'''
        if banner.lower() not in ["character", "weapon", "standard"]:
            await self.bot.send_failure_message(ctx.message.channel,'```Invalid banner, must be one of: "character", "weapon", "standard"```')
            return
        try:
            urlparse(feedback_url)
        except:
            await self.bot.send_failure_message(ctx.message.channel,'```<feedback_url> is not a valid url.```')
            return
        async with ctx.typing():
            r = self.cursor.execute('''SELECT 1 FROM pity_rate_limit WHERE (julianday('now')-last_request < 1./24/60/2) AND user_id=?''',(ctx.author.id,))
            if r.fetchone():
                await self.bot.send_failure_message(ctx.message.channel,'```Too many requests in a short period, please try again later.```')
                return
            self.cursor.execute('''REPLACE into pity_rate_limit (user_id, last_request) VALUES (?, julianday('now'))''',(ctx.author.id,))
            self.conn.commit()
            e = await self.getPityEmbed(feedback_url, banner)
            if not e:
                await self.bot.send_failure_message(ctx.message.channel,'```Failed to fetch wish history.```')
                return
            await self.bot.send_message(ctx.message.channel, embed = e)
        
    @tasks.loop(seconds=60*RESIN_REGEN_IN_MINUTES)
    async def resinalert(self):
        r = self.cursor.execute('''SELECT user_id FROM resin WHERE round((julianday('now')-timestamp)/(?/(24.*60))-.5) + amount = ?''',(RESIN_REGEN_IN_MINUTES,RESIN_CAP))
        for row in r.fetchall():
            try:
                user = await self.bot.fetch_user(row[0])
                dm_channel = await user.create_dm()
                await dm_channel.send('```Your resin is full!```')
            except:
                'channel missing or bot is blocked'
                
    @resinalert.before_loop
    async def before_run(self):
        await self.bot.wait_until_ready()
        
def setup(bot):
    bot.add_cog(GenshinTools(bot))