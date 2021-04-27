# PoE-Info
Path of Exile Discord bot with some simple wiki data and forum commands.

### [Add PoE-Info to your server](https://discordapp.com/oauth2/authorize?client_id=313788924151726082&scope=bot&permissions=420928)
#### Because PoE-Info has reached the maximum number of allowed servers (without verification, see below) I have made a [public server](https://discord.gg/Z7fZvVMQSH) you can join if you just want to use the bot personally. By joining this server you will be able to DM the bot to use commands.
##### It has come to my attention that discord bots can no longer join more than 100 servers unless the owner completes a [verification process](https://blog.discordapp.com/4e6e050ab52e) requiring them to hand over personal information which I do _NOT_ plan on doing. Because of this, if you have this bot in a server which no longer needs it (such as a dead server), I kindly ask that you remove the bot from that server so someone else can add it to theirs. Alternatively, I suggest hosting a local copy of this bot if possible (and sharing it with others if you want to help out more.)
![Example Image](https://raw.githubusercontent.com/NeverDecaf/PoE-Info/master/sample.PNG)

Here is a (maybe not exhaustive) list of commands, run -help to see the updated list:
- help -- List all commands
- unique `alias: -u` -- Shows stats for a unique item
- unique search `alias: -us` -- Search item explicits for keywords
- skill `alias: -s` --   Shows stats for a skill gem
- currency `alias: -c` -- Show Chaos rate for a currency item
- node `alias: -n` -- Shows description of a notable or keystone
- node search `alias: -ns` -- Search node description for keywords
- next     --    Displays the upcoming race
- announcements -- Toggle notifications for forum announcements
- patchnotes  -- Toggle notifications for patch note posts
- events   -- Toggle notifications for events (races)
- deals   -- Toggle notifications for daily deal
- deals filter  -- Set regexp filter to only show matching deals
- pcleague -- Sets league used for pricing items (per-channel)
- lab	-- Get daily lab layout from poelab.com

- pin	-- (Automatically) moves pins to a different channel (to overcome discord pin limit)
- reminder	-- Set reminders (set -reminder timezone first)

type -help <command> for more info on any of these

#### To run your own instance
1. Run db.py and let it finish (might take a while)
after this initial update you should run this file on a schedule to keep your database up-to-date
2. Put your discord bot token in a file called "token" and run bot.py
