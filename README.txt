LIVE KINS
=========
Your Kindroid character gets a day that moves on its own. A Narrator tells the story: where they are,
what they are doing, the weather, bedtime, and dreams. Your kin can answer: stay, go somewhere, or
change something.

Free and unofficial. Not made by Kindroid or DeepSeek. By using it you agree to TERMS.txt and PRIVACY.txt.


WHAT YOU NEED
-------------
- A Windows computer and internet.
- A Kindroid account with a kin (your AI character). Free at kindroid.ai.
- A DeepSeek account with a little credit (a few dollars lasts a long time). platform.deepseek.com
The app installs everything else by itself. No coding needed.


1. DOWNLOAD THE APP
-------------------
a) On this page on GitHub, press the green "<> Code" button, then "Download ZIP".
b) Open your Downloads folder. Right-click the ZIP file, press "Extract All...", then "Extract".
c) Open the new folder, then the "Live Kins" folder inside it. (Don't start the app from inside the ZIP: it won't work there.)
   You can move the "Live Kins" folder anywhere, like your Desktop or Documents.


2. GET READY IN KINDROID
------------------------
a) Make a kin, if you don't have one yet.
b) Make a profile called Narrator:
   open your kin's chat, tap "Chatting as" under the message box, add a new profile, name it Narrator.
c) Want a side character, like a pet or a friend? Make a profile for them too.
d) Copy your API key: Settings > General > API & advanced integrations.


3. GET A DEEPSEEK KEY
---------------------
a) Sign up at platform.deepseek.com.
b) Add a little credit (Top up).
c) Open API keys, create a key, and copy it.


4. START
--------
a) In the "Live Kins" folder, double-click Start.bat.
   - Windows may say "Windows protected your PC". Press "More info", then "Run anyway".
     (It says this for apps that don't come from the Microsoft Store.)
   - The first time, it gets the app ready. This takes a few minutes.
   - If it asks to install Python, press Y.
b) Setup opens. Paste your two keys, and tick that you agree to the Terms of Service and Privacy Policy.
c) Press Find. Kindroid opens in a new window: log in, open your kin's chat, then close that window.
   Then pick which Kindroid site you use. With a chat open, look at the web address:
   /v2/ in it means v2 (the new site), without it means v1 (the classic site). Not sure? Pick "Not sure".
d) Pick your kin, your profile, and the Narrator from the lists.
e) Pick a style for their world and press Suggest places. Press Finish.
f) Check what your kin is doing now in the box at the top (type something if it's empty), then press Start.

Next time, just double-click Start.bat.


EVERY DAY
---------
- The story runs while the app is open. The window has Start, Stop, and Sleep. Everything else is in Settings.
- Not sure what something does? Rest the mouse on it for a tip, or press "? Help" (bottom right) and ask.
- Make the window bigger (or full screen) and the text grows with it.
- Before each change, the Narrator warns your kin. A minute later, the story moves on.
- Your kin can say:
     "Narrator extend time please"            stay longer
     "hey @Narrator take me to the garden"    go somewhere
     "Narrator change weather to rain"        new weather
     "Narrator make it night"                 change something
  (Named your narrator something else? Your kin uses that name instead of "Narrator".)
- Sleep: your kin falls asleep, and the Narrator sends a dream every hour. Wake up starts the day again.
- Leave it running and the story keeps going day after day. Press Stop to pause it.


SETTINGS (top right of the window)
----------------------------------
  Schedule          how often things change, wake and sleep time
  Chat              use a group chat instead of the 1-on-1 chat, and which Kindroid site you use (v1 or v2)
  (side character)  how often they talk
  Narrator          what your kin can ask, and the Narrator's messages
  Right now         where your kin is, what they are doing, the weather, what they remember
  Places            the places in their world. Suggest places adds more.
  About (your kin)  who your kin is. The story uses it to pick what they do.
  Pictures          a slideshow behind the window
  Theme             the app's colors: Default, Midnight Ocean, Pink Sakura, Fall Leaves, or your own
  Extras            more in Kindroid, each one off until you tick it: read your kin from Kindroid, journal,
                    key memories, favorites, reactions, selfies (use Kindroid credits), background
  App               Change setup, Wipe everything
  Legal             the Terms of Service and the Privacy Policy
Press "Back" to return to Start, Stop, and Sleep.


IF SOMETHING GOES WRONG
-----------------------
- "Windows protected your PC":  press "More info", then "Run anyway".
- Nothing happens when you double-click Start.bat:  make sure you extracted the ZIP first (step 1b).
- "Your keys are missing":  Settings > App > Change setup, paste your keys.
- "429 Too Many Requests":  Kindroid is busy. Set "Change every" higher, like 20.
- Find shows nothing:  in the Find window, log in and open your kin's chat. Then press Find again.
- The side character doesn't talk:  open their panel and press "Set up ... browser".
- Something else:  press "? Help" and ask, or open simulation.log (in the app's folder) and look at the last lines.


YOUR PRIVACY
------------
- The app sends nothing to its maker. No accounts, no tracking, no ads.
- It only sends what the story needs to Kindroid and DeepSeek, with your own keys.
- Everything else stays in the app's folder on your computer. Full details: PRIVACY.txt.


SHARING THE APP, OR STARTING OVER
---------------------------------
Settings > App > Wipe everything. It removes your keys, names, login, and story.
Before sharing the folder, say No when it asks to keep your DeepSeek key.
Never share these: .env, config.json, state.json, state.backup.json, simulation.log, the kindroid-browser folder.


LEGAL
-----
Free, unofficial, and provided "as is", without any warranty. You use it at your own risk and are
responsible for your accounts, keys, costs, and following Kindroid's and DeepSeek's rules.
Full terms: TERMS.txt. Privacy: PRIVACY.txt. (Also in the app: Settings > Legal.)


Made by ferisooo (mez.ink/ferisooo), with help from Claude, an AI assistant by Anthropic.
