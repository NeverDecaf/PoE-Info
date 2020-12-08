from discord.ext import commands
import sqlite3
RESIN_CAP = 160
RESIN_REGEN_IN_MINUTES = 8
SMALLEST_SPENDABLE_RESIN = 20 # used for -resin reset
class ResinTimer(commands.Cog, name='Resin Timer'):
    def __init__(self, bot):
        self.bot = bot
        self.conn = sqlite3.connect('resin.sqlitedb')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS resin
             (user_id int,
             amount int,
             timestamp real,
             PRIMARY KEY (user_id))''')
    @commands.command()
    async def resin(self, ctx, *amt):
        ''' [<current amount|reset>] 
        Can also provide a negative amount to modify or use -resin reset to remove largest multiple of 20 (will leave <20 remaining).'''
        if len(amt):
            try:
                assert int(amt[0])<=RESIN_CAP
                if int(amt[0])<0:
                    self.cursor.execute(''' UPDATE resin set amount=amount+? where user_id=?''',(int(amt[0]),ctx.author.id,))
                else:
                    self.cursor.execute(''' REPLACE INTO resin (user_id,amount,timestamp)  VALUES (?,?,julianday('now')) ''',(ctx.author.id,int(amt[0])))
                self.conn.commit()
            except:
                if amt[0].lower() == 'reset':
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
def setup(bot):
    bot.add_cog(ResinTimer(bot))