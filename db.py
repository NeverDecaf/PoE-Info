import scrape_poe_wiki
import sqlite3
import requests
import re
import json
import html
import time
import os

abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

class PoeDB:

    def __init__(self,ro=False,dbfile="poedb.sqlite"):
        self.db=dbfile
        self._connect(ro)
        self._create_tables()
    
    def _connect(self, ro):
        if ro:
            self.conn=sqlite3.connect('file:%s?mode=ro'%self.db, uri=True)
        else:
            self.conn=sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        self.cursor=self.conn.cursor()
    
    def _create_tables(self):
        field_names = scrape_poe_wiki.UNIQUE_ITEM_PROPERTY_MAPPING.values()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS unique_items
                 (thumbnail_url text, {}, PRIMARY KEY (name))'''.format(','.join([name+' text' for name in field_names])))
        field_names = scrape_poe_wiki.SKILL_GEM_PROPERTY_MAPPING.values()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS skill_gems
                 (thumbnail_url text, {}, PRIMARY KEY (name))'''.format(','.join([name+' text' for name in field_names])))
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS event_times
                 (id text primary key, startAt timestamp, endAt timestamp, url text)''')
        self.conn.commit()

    def add_item(self,data,table='unique_items'):
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?']*len(data.values()))
        query = '''REPLACE INTO %s (%s) VALUES (%s)''' % (table, columns, placeholders)
        self.cursor.execute(query, [v if v==None else html.unescape(str(v)) for v in list(data.values())])
        self.conn.commit()
        
    def get_data(self,tablename,searchname,limit = 9):
        # just use a LIKE
        query = '''SELECT * FROM {} WHERE name COLLATE NOCASE LIKE "%"||?||"%" LIMIT {}'''.format(tablename,limit)
        res=self.cursor.execute(query,(searchname.lower(),))
        return res.fetchall()
    
    def upcoming_event(self,warning_intervals=[5]):
        r=self.cursor.execute('''SELECT id || ' Starting in ' || CAST(strftime('%M',julianday(startAt)-julianday('now','-30 seconds')) AS INTEGER) || ' Minutes!' from event_times where strftime('%s',startAt) IN ({})'''.format(','.join(["strftime('%%s',datetime('now','+%i minutes'))"%x for x in warning_intervals])))
        return r.fetchall()
    
    def _scrape_events(self):
        data = requests.get('http://api.pathofexile.com/leagues?type=event&compact=1')
        js=json.loads(data.text)
        if len(js):
            self.cursor.execute('''DELETE FROM event_times''')
        for event in js:
            self._insert_data(event,'event_times')
            
    def _get_images(self,table):
        query = '''SELECT name,image_url FROM {} WHERE thumbnail_url IS NULL'''.format(table)
        pairs = self.cursor.execute(query).fetchall()
        for name,url in pairs:
            thumb_url = scrape_poe_wiki.get_image_url(name,url,is_div_card=False)
            self.cursor.execute('UPDATE {} set thumbnail_url=? where name=?'.format(table),(thumb_url,name))
            time.sleep(1)
        
    def _insert_data(self, data, table):
        '''
        Generic data insertion for the db. Does a REPLACE which will overwrite existing data.

        data is a dict of data where keys are equivalent to columns in the db.
        table is the db table name into which you are inserting data.
        '''
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?']*len(data.values()))
        query = '''REPLACE INTO %s (%s) VALUES (%s)''' % (table, columns, placeholders)
        self.cursor.execute(query, [v if v==None else str(v) for v in list(data.values())])
        self.conn.commit()
        
    def event_ending(self):
        #only report this if the current event just ended.
        r=self.cursor.execute('''SELECT id from event_times where strftime('%s',endAt)=strftime('%s',datetime('now'))''')
        ending = r.fetchone()
        if ending:
            out = '-%s Has Ended.'%ending[0]
            nexte = self.next_event()
            if nexte:
                out += '\n+Next Event: %s'%nexte
            return out
        else:
            return None
        
    def next_event(self):
        r=self.cursor.execute('''SELECT id,strftime('%Hh%Mm',time((julianday(startAt)-julianday('now','-30 seconds'))*86400,'unixepoch')) from event_times where strftime('%s',startAt)>strftime('%s',datetime('now','-1 minutes')) order by startAt limit 1''')
        nexte = r.fetchone()
        if nexte:
            return '%s Begins in %s.'%tuple(nexte)
        return None

    def close(self):
        self.conn.close()
        self.conn=None
        self.cursor=None

if __name__=='__main__':
    a = PoeDB()
    
    #scrape uniques
    for unique in scrape_poe_wiki.format_affixes(scrape_poe_wiki.scrape_unique_items()):
        a.add_item(unique)
    #scrape skill gems
    for gem in scrape_poe_wiki.scrape_skill_gems():
        a.add_item(gem,'skill_gems')
    # get thumbnail images (SLOW) (1s pause per item in addition to page load times)
    a._get_images('unique_items')
    a._get_images('skill_gems')
    
    a.close()



