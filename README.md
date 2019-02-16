# PoE-Info
Path of Exile Discord bot with some simple wiki data and forum commands.

Here is a (maybe not exhaustive) list of commands:
- help -- List commands
-  unique -- Shows stats for a unique item.
-  skill  --   Shows stats for a skill gem.
- next     --    Displays the upcoming race.
-  announcements -- Toggle notifications for forum announcements
-  patchnotes  -- Toggle notifications for patch note posts
-  events   -- Toggle notifications for events (races)
-  deals   -- Toggle notifications for daily deals

type -help <command> for more info on any of these

#### To Run
1. Run db.py and let it finish (might take a while)
after this initial update you should run this file on a schedule to keep your database up-to-date
2. Put your discord bot token in a file called "token" and run bot.py

#### If you just want this bot in your server
You can add it via this [link](https://discordapp.com/oauth2/authorize?client_id=313788924151726082&scope=bot&permissions=0).
This is hosted on an rPi along with some other bots so please host a copy yourself if possible.
