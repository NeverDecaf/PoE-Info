#!/usr/bin/env python3
import scrape_poe_wiki
import sqlite3
import requests
import re
import json
import html
import time
import os
import cloudscraper

abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)
VALID_PC_LEAGUES = ['tmpStandard', 'tmpHardcore', 'eventStandard', 'eventHardcore', 'Standard', 'Hardcore']
class PoeDB:

    def __init__(self,ro=False,dbfile="poedb.sqlite"):
        self.db=dbfile
        self.ro = ro
        self._connect(ro)
        self._create_tables()
        self.scraper = cloudscraper.create_scraper()
    
    def _connect(self, ro):
        if ro:
            self.conn=sqlite3.connect('file:%s?mode=ro'%self.db, uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
        else:
            self.conn=sqlite3.connect(self.db, detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.row_factory = sqlite3.Row
        def _trim_variant(itemname):
            return re.sub(' ?\([^)]*\)','',itemname)
        self.conn.create_function('trim_variant',1,_trim_variant)
        self.cursor=self.conn.cursor()
        # self.cursor.execute('pragma short_column_names=OFF;')
        # self.cursor.execute('PRAGMA full_column_names=ON;')
    
    def _create_tables(self):
        field_names = scrape_poe_wiki.UNIQUE_ITEM_PROPERTY_MAPPING.values()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS unique_items
                 (thumbnail_url text, {}, PRIMARY KEY (name))'''.format(','.join([name+' text' for name in field_names])))
        field_names = scrape_poe_wiki.SKILL_GEM_PROPERTY_MAPPING.values()
        levelmax_field_names = scrape_poe_wiki.SKILL_GEM_VARIABLE_FIELDS.values()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS skill_gems
                 (thumbnail_url text, skill_id_group text, {}, {}, PRIMARY KEY (name))'''.format(','.join([name+' text' for name in field_names]),','.join([name+'_max text' for name in levelmax_field_names])))
        field_names = scrape_poe_wiki.SKILL_QUALITY_PROPERTY_MAPPING.values()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS skill_quality
                 ({}, PRIMARY KEY (name,q_type))'''.format(','.join([name+' text' for name in field_names])))
        field_names = scrape_poe_wiki.PASSIVE_SKILLS_PROPERTY_MAPPING.values()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS passive_skills
                 (thumbnail_url text, {}, PRIMARY KEY (pagename, name))'''.format(','.join([name + (' integer' if name.startswith('is_') else ' text') for name in field_names])))
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS event_times
                 (id text primary key, startAt timestamp, endAt timestamp, url text)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS ninja_data
                 (id integer, name text, icon text, chaosValue real, exaltedValue real, divineValue real, itemClass integer, league text, PRIMARY KEY (id,league))''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS ninja_currency_data
                 (id integer, name text, icon text, chaosValue real, league text, PRIMARY KEY (id,league))''')
        # add timestamp triggers to poe.ninja tables
        for table in ('ninja_currency_data','ninja_data'):
            try:
                self.cursor.execute(f'''ALTER TABLE {table} ADD COLUMN timestamp timestamp DEFAULT 0''')
                self.cursor.execute(f'''UPDATE {table} SET timestamp = datetime('now') where timestamp = 0''')
            except sqlite3.OperationalError:
                pass
            try:
                self.cursor.execute(f'''CREATE TRIGGER {table}_inserttime AFTER INSERT ON {table}
                begin
                update {table} set timestamp=datetime('now') where id = new.id;
                end''')
            except sqlite3.OperationalError:
                pass
            try:
                self.cursor.execute(f'''CREATE TRIGGER {table}_updatetime AFTER UPDATE ON {table}
                begin
                update {table} set timestamp=datetime('now') where id = old.id;
                end''')
            except sqlite3.OperationalError:
                pass
        try:
            self.cursor.execute(f'''ALTER TABLE ninja_data ADD COLUMN divineValue real''')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute(f'''ALTER TABLE skill_gems ADD COLUMN skill_id_group text''')
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def add_item(self,data,table='unique_items'):
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?']*len(data.values()))
        query = '''REPLACE INTO %s (%s) VALUES (%s)''' % (table, columns, placeholders)
        self.cursor.execute(query, [v if v==None else html.unescape(str(v)) for v in list(data.values())])
        self.conn.commit()

    def add_items_async(self,data,table='unique_items'):
        self.cursor.execute('''CREATE TEMP TABLE {}_tmp AS SELECT * FROM {} LIMIT 0'''.format(table,table))
        for datum in data:
            columns = ', '.join(datum.keys())
            placeholders = ', '.join(['?']*len(datum.values()))
            query = '''REPLACE INTO {}_tmp ({}) VALUES ({})'''.format(table, columns, placeholders)
            self.cursor.execute(query, [v if v==None else html.unescape(str(v)) for v in list(datum.values())])
        self.cursor.execute('''REPLACE INTO {} SELECT * FROM {}_tmp'''.format(table,table))
        self.cursor.execute('''DROP TABLE {}_tmp'''.format(table))
        self.conn.commit()
        
    def get_data(self,tablename,searchname,league = None,limit = 9, search_by_baseitem = False):
        query = '''SELECT * FROM {} 
        left join ninja_data 
        on trim_variant({}.name)=ninja_data.name AND ninja_data.league=? 
        COLLATE NOCASE WHERE {}.{} COLLATE NOCASE LIKE "%"||?||"%" {} AND COALESCE(itemClass,0) <> 9
        GROUP BY {}.name 
        ORDER BY MAX(chaosValue) 
        LIMIT {}'''.format(tablename,tablename,tablename,'baseitem' if search_by_baseitem else 'name', 'AND drop_enabled' if league not in ('Standard','Hardcore') and tablename=='unique_items' else '', tablename, limit)
        res=self.cursor.execute(query,(league,searchname.lower(),))
        ret = res.fetchall()
        if len(ret)>1:
            for entry in ret:
                if entry['name'].lower()==searchname.lower():
                    return [entry]
        return ret
        
    # split off into its own function thanks to alt quality.
    def get_skill_data(self,tablename,searchname,league = None,limit = 9, search_by_baseitem = False):
        price_data_to_keep = ['chaosValue','exaltedValue','divineValue']
        query = f'''
            WITH skill_groups AS (
            SELECT skill_id_group
            FROM {tablename}
            WHERE {tablename}.{'baseitem' if search_by_baseitem else 'name'} COLLATE NOCASE LIKE "%"||?||"%"
            {'AND drop_enabled' if league not in ('Standard','Hardcore') and tablename=='unique_items' else ''}
            GROUP BY {tablename}.skill_id_group
            LIMIT {limit}
        )
        SELECT *, qual_bonus as qual_bonus_normal,
        {','.join([i+'.'+k+' as '+i+'_'+k for i in ('p_n',) for k in price_data_to_keep])}
        FROM {tablename}
        LEFT JOIN ninja_data p_n ON {tablename}.name = p_n.name AND p_n.league = ? COLLATE NOCASE
        WHERE {tablename}.skill_id_group IN (SELECT skill_id_group FROM skill_groups)
        {'AND drop_enabled' if league not in ('Standard','Hardcore') and tablename=='unique_items' else ''}
        ORDER BY {tablename}.name COLLATE NOCASE LIKE '%' || ? || '%' DESC, {tablename}.skill_id_group == {tablename}.skill_id DESC
        '''
        res=self.cursor.execute(query,(searchname.lower(),league,searchname.lower(),))
        # print(query.replace('?',f"'{league}'"),searchname.lower())
        ret = res.fetchall()
        # now you need to group results by skill_id_group to batch trans/vaal gems
        grouped = self._group_by_row(ret,'skill_id_group')
        if len(grouped)>1:
            for group in grouped:
                for entry in group['list']:
                    if entry['name'].lower()==searchname.lower():
                        return [group]
        return grouped
        
    def _group_by_row(self, data, row_name):
        # data is a list of sqlite Row objects (probably)
        # will return a list of lists of Row objects, each one is a grouping of results with the same value for row_name
        # for example, each list is a list of skills with the same skill_id_group root, (vaal cold snap, cold snap, cold snap of power)
        buckets = {}
        for p in data:
            key = p[row_name]
            if key not in buckets:
                buckets[key] = {'name':p['name'], 'list':[p]}
            else:
                buckets[key]['list'].append(p)
        return list(buckets.values())

    def unique_search_explicit(self,keywords,league,limit = 9):
        query = '''SELECT * FROM unique_items left join ninja_data on unique_items.name=ninja_data.name AND ninja_data.league=? COLLATE NOCASE WHERE unique_items.expl COLLATE NOCASE LIKE "%"||?||"%" COLLATE NOCASE '''
        for i in range(len(keywords)-1):
            query+= 'AND unique_items.expl COLLATE NOCASE LIKE "%"||?||"%" COLLATE NOCASE '
        if league not in ('Standard','Hardcore'):
            query += 'AND drop_enabled'
        query+=''' GROUP BY unique_items.name ORDER BY MAX(chaosValue) LIMIT {}'''.format(limit)
        res=self.cursor.execute(query,(league,*keywords))
        return res.fetchall()

    def passive_search_description(self,keywords,limit = 9):
        query = '''SELECT * FROM passive_skills WHERE passive_skills.desc COLLATE NOCASE LIKE "%"||?||"%" COLLATE NOCASE '''
        for i in range(len(keywords)-1):
            query+= 'AND passive_skills.desc COLLATE NOCASE LIKE "%"||?||"%" COLLATE NOCASE '
        query+=''' LIMIT {}'''.format(limit)
        res=self.cursor.execute(query,keywords)
        return res.fetchall()
        
    def get_currency(self,searchname,league,limit = 9,exact = False):
        query = '''SELECT * FROM ninja_currency_data WHERE ninja_currency_data.league=? COLLATE NOCASE AND ninja_currency_data.name COLLATE NOCASE LIKE "%"||?||"%" LIMIT ?'''
        if exact:
            query = '''SELECT * FROM ninja_currency_data WHERE ninja_currency_data.league=? COLLATE NOCASE AND ninja_currency_data.name COLLATE NOCASE = ? COLLATE NOCASE LIMIT ?'''
        res=self.cursor.execute(query,(league,searchname.lower(),limit))
        return res.fetchall()
    
    def upcoming_event(self,warning_intervals=[5]):
        r=self.cursor.execute('''SELECT id || ' Starting in ' || CAST(strftime('%M',julianday(startAt)-julianday('now','-30 seconds')) AS INTEGER) || ' Minutes!' from event_times where strftime('%s',startAt) IN ({})'''.format(','.join(["strftime('%%s',datetime('now','+%i minutes'))"%x for x in warning_intervals])))
        return r.fetchall()
    
    def _scrape_events(self):
        data = self.scraper.get('http://api.pathofexile.com/leagues?type=event&compact=1')
        try:
            js = data.json()
        except json.decoder.JSONDecodeError:
            js = {}
        if len(js):
            self.cursor.execute('''DELETE FROM event_times''')
        for event in js:
            self._insert_data(event,'event_times',ignore_nonexistant_cols=True)
            
    def _get_images(self,table):
        query = '''SELECT name,image_url FROM {} WHERE thumbnail_url IS NULL'''.format(table)
        pairs = self.cursor.execute(query).fetchall()
        for name,url in pairs:
            thumb_url = scrape_poe_wiki.get_image_url(name,url,is_div_card=False)
            self.cursor.execute('UPDATE {} set thumbnail_url=? where name=?'.format(table),(thumb_url,name))
            time.sleep(1)            
        
    def _insert_data(self, data, table, ignore_nonexistant_cols=False):
        '''
        Generic data insertion for the db. Does a REPLACE which will overwrite existing data.

        data is a dict of data where keys are equivalent to columns in the db.
        table is the db table name into which you are inserting data.
        '''
        if ignore_nonexistant_cols:
            r = self.cursor.execute('''PRAGMA table_info({})'''.format(table))
            cols = tuple([m[1] for m in r.fetchall()])
            data = data.copy()
            for todel in [c for c in data.keys() if c not in cols]:
                data.pop(todel)
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

    def reset(self):
        self.cursor.execute('''DROP TABLE unique_items''')
        self.cursor.execute('''DROP TABLE skill_gems''')
        self.cursor.execute('''DROP TABLE ninja_data''')
        self.cursor.execute('''DROP TABLE ninja_currency_data''')
        self._create_tables()
        self.conn.commit()

if __name__=='__main__':
    import sys
    import datetime
    
    a = PoeDB()
    a._scrape_events()
    print(datetime.datetime.now())
    if len(sys.argv)>1 and sys.argv[1]=='-r':
        a.reset()
    if len(sys.argv)>1 and sys.argv[1]=='-pc':
        pass #pricecheck only
    else:
        #scrape uniques
        a.add_items_async(scrape_poe_wiki.format_affixes(scrape_poe_wiki.scrape_unique_items()))
        #scrape skill gems
        a.add_items_async(scrape_poe_wiki.scrape_skill_gems(),'skill_gems')
        #scrape skill quality
        a.add_items_async(scrape_poe_wiki.scrape_skill_quality(),'skill_quality')
        #scrape passive skills
        a.add_items_async(scrape_poe_wiki.scrape_passive_skills(),'passive_skills')
        #scape events (RIP)
        a._scrape_events()
    # get poe.ninja data (mainly for price)
    for league in VALID_PC_LEAGUES:
        data = scrape_poe_wiki.get_ninja_prices(league)
        if data:
            a.add_items_async(data,'ninja_data')
        data = scrape_poe_wiki.get_ninja_rates(league)
        if data:
            a.add_items_async(data,'ninja_currency_data')
    a.close()
