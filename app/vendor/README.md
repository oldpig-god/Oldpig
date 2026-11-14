# AKShare THS adapter asset

`akshare-ths.js` is copied unchanged from AKShare 1.18.64 (`akshare/data/ths.js`). Its MIT license is included in `AKShare-LICENSE`. Source project: https://github.com/akfamily/akshare .

Only this locally shipped script runs in MiniRacer (1-second execution limit, 32 MiB JS heap limit); remote JavaScript is never executed. The adapter follows AKShare's public THS catalog request convention. Generated request cookies are ephemeral, are sent only to the THS source and are not included in reports or logs.
