# PoE-Info
Path of Exile Discord bot with some simple wiki data and forum commands.

#### [Add PoE-Info to your server](https://discordapp.com/oauth2/authorize?client_id=313788924151726082&scope=bot&permissions=0)

![Example Image](https://raw.githubusercontent.com/NeverDecaf/PoE-Info/master/sample.PNG)

Here is a (maybe not exhaustive) list of commands, run -help to see the updated list:
- help -- List all commands
- unique `alias: -u` -- Shows stats for a unique item.
- unique search `alias: -us` -- Search item explicits for keywords
- skill `alias: -s` --   Shows stats for a skill gem.
- currency `alias: -c` -- Show Chaos rate for a currency item.
- next     --    Displays the upcoming race.
- announcements -- Toggle notifications for forum announcements
- patchnotes  -- Toggle notifications for patch note posts
- events   -- Toggle notifications for events (races)
- deals   -- Toggle notifications for daily deal
- deals filter  -- Set regexp filter to only show matching deals.
- pcleague -- Sets league used for pricing items (per-channel).
- lab	-- Get daily lab layout from poelab.com

- pin	-- (Automatically) moves pins to a different channel (to overcome discord pin limit)
- reminder	-- Set reminders (set -reminder timezone first)

type -help <command> for more info on any of these

#### To run your own instance
1. Run db.py and let it finish (might take a while)
after this initial update you should run this file on a schedule to keep your database up-to-date
2. Put your discord bot token in a file called "token" and run bot.py
