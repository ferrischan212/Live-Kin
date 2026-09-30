"""What every part of the app does: the hover tips, and the guide the Help chat answers from.

Written with the example names (Lora, Mochi, Master, Narrator). The app shows the names from setup instead.
"""

from __future__ import annotations

# Hover tips. The key is the text on the option, as the app writes it (before the names from setup go in).
TIPS = {
    # The main window
    "Settings": "Opens Settings: the schedule, the side character, the Narrator, places, pictures, and setup.",
    "What Lora is doing now": "What Lora is doing right now. It changes by itself. You can also type a new one before pressing Start.",
    "Start": "Starts the story. The Narrator warns Lora, and a minute later moves the story on.",
    "Stop": "Stops the story. Nothing is sent to Kindroid until you press Start again.",
    "Sleep": "Lora falls asleep. The story pauses, and the Narrator sends a dream every hour until Wake up.",
    "Wake up": "Lora wakes up and the story carries on.",
    "Dream now": "Sends the next dream right away instead of waiting an hour.",
    "Help": "Ask the helper anything about this app.",
    # The Settings buttons
    "Schedule": "How often the story moves on, and when the day starts and ends.",
    "Chat": "Use a Kindroid group chat instead of Lora's 1-on-1 chat.",
    "Mochi": "The side character (like a pet or a friend) who talks every few minutes.",
    "Narrator": "What Lora can ask the Narrator, and the Narrator's warning and dream messages.",
    "Right now": "Where Lora is, what they are doing, the weather, their goal, and what they remember.",
    "Places": "Every place in Lora's world. The story only goes to places on this list.",
    "About Lora": "Who Lora is. The story reads this to pick things Lora would do.",
    "Pictures": "A slideshow of your own pictures behind the main window.",
    "App": "Change setup, wipe everything, and updates.",
    "Theme": "The app's colors: pick a ready-made theme, or your own colors.",
    "Legal": "The Terms of Service and the Privacy Policy.",
    "Extras": "Extra things the app can do in Kindroid: journal, key memories, favorites, reactions, selfies, background.",
    "Know Lora: read Lora's backstory and key memories from Kindroid each morning":
        "Each morning the app reads Lora's backstory, key memories, and directive from Kindroid, so the story fits Lora.",
    "Journal: write the places Lora finds, and each day, into Lora's Kindroid journal":
        "New places and a short note about each day go into Lora's Kindroid journal. When a place comes up in chat, Lora remembers it.",
    "Key memories: add the story's big moments to Lora's key memories":
        "Big moments (a new place, each day) are added at the end of Lora's key memories. Lora's own key memories stay as they are.",
    "Favorites: pin the Narrator's dreams and discoveries": "Dreams and discoveries get pinned (Favorite) in Lora's chat.",
    "Mochi reacts to Lora's messages (on Mochi's turn, new Kindroid site)":
        "Sometimes Mochi adds an emoji reaction to Lora's last message, when it is Mochi's turn to talk.",
    "Selfies: Lora sends a selfie at new places (uses Kindroid credits)":
        "When Lora gets to a new place, Lora sends a selfie of it. Each selfie uses Kindroid credits.",
    "Background: the chat background shows Lora's latest selfie":
        "Kindroid's chat background shows Lora's newest selfie. With Selfies on, the background follows where Lora is.",
    "Selfies a day": "The most selfies a day, so the credits last.",
    "Read Lora from Kindroid now": "Reads Lora's backstory and key memories from Kindroid right now. The About page gets the backstory.",
    "Terms of Service": "The rules for using this app. By using it you agree to them.",
    "Privacy Policy": "What the app keeps and sends. It sends nothing to the maker.",
    "mez.ink/ferisooo": "Made by ferisooo, with help from Claude (an AI by Anthropic). Opens mez.ink/ferisooo in your browser.",
    # Schedule
    "Change every": "Minutes between changes, like 10. Or hours, like 2h.",
    "Also at": "Extra change times each day, like 9:00 AM, noon, night. Optional.",
    "Wake at": "When Lora's day starts. The app starts the day by itself at this time.",
    "Sleep at": "When Lora falls asleep and starts dreaming. Leave empty if Lora should never fall asleep by themself.",
    "Each moment lasts as long as it would in real life":
        "On: a quick snack lasts 5 minutes, a bath 30, dinner 45. Off: every change waits the same Change every time.",
    "Warn Lora 1 minute before each change":
        "The Narrator tells Lora a change is coming, so Lora can ask to stay longer or go somewhere else.",
    # Chat
    "Use the group chat instead of the 1-on-1 chat":
        "The Narrator talks in your group chat instead. Pick the group in App > Change setup first.",
    "Kindroid site": "Which Kindroid site you use: v1 (classic) or v2 (new, /v2/ in the web address). "
        "The side character's wand works on both. Not sure? Let the app check by itself.",
    "My group chat is set to Auto in Kindroid":
        "Tick this if your group lets Kindroid pick who answers. Then the app only reads Lora's answer.",
    # The side character
    "Mochi talks every few minutes": "Mochi says something by themself every few minutes, while the story runs.",
    "Talk every": "Minutes between Mochi's lines.",
    "Set up Mochi's browser": "Only if Mochi can't talk: log in to Kindroid once in the app's own browser.",
    "Mochi's AI ID": "Only for a group chat: Mochi's own kin ID, so Mochi can take a turn there.",
    # Narrator
    "What Lora can ask the Narrator": "Words Lora can say, and what happens then. Change them, or add your own.",
    "Lora says": "The words Lora says to the Narrator.",
    "What happens": "What the Narrator does when Lora says them.",
    "+ Add command": "Adds a new line: words Lora can say, and what happens.",
    "Warning, 1 minute before a change": "The message the Narrator sends before each change. {where} and {wait} fill in by themselves.",
    "Before each dream": "What the Narrator says to Lora before each dream.",
    "Save": "Keeps your changes to the Narrator's words.",
    "Preview": "Shows the warning message the way Lora will see it.",
    "Reset to default": "Goes back to the Narrator's original words.",
    # Places
    "Suggest places": "Adds more places that fit Lora's world.",
    # Pictures
    "Add pictures": "Pick pictures from your computer for the slideshow behind the window.",
    "Remove all": "Removes the slideshow pictures.",
    "Visible": "How strongly the picture shows behind the window.",
    "Next every (sec)": "Seconds before the next picture.",
    # App
    "Change setup (names, profiles, keys, places)": "Opens setup again: your keys, your kin, profiles, the group chat, and places.",
    "Wipe everything and start over": "Removes your keys, names, login, and story. Use it before sharing the app.",
    "Restart by itself after an update": "When the app's files are updated, it restarts at a quiet moment.",
    # Theme
    "Ready-made themes": "One click changes every color. Each one is easy to read.",
    "Default": "The app's own purple theme.",
    "Midnight Ocean": "Deep night blues.",
    "Pink Sakura": "Light pink, like cherry blossoms.",
    "Fall Leaves": "Warm browns and orange.",
    "Your own colors": "Click a color square to pick your own. The note below says if something gets hard to read.",
    "Background": "The color behind everything.",
    "Buttons and panels": "The color of the buttons.",
    "Text": "The color of most words.",
    "Soft text and hints": "The color of small hints and labels.",
    "Boxes you type in": "The color inside the boxes you type in.",
    "Highlight (the lit button)": "The color of the button that is on, and the Help bubble. Its text turns black or white by itself.",
    "Your kin's name": "The color of Lora's name at the top.",
    # Right now
    "Place": "Where Lora is.",
    "Doing": "What Lora is doing.",
    "Here since": "How long Lora has been here.",
    "Stayed longer": "How many times in a row Lora asked to stay. After 8, the Narrator moves Lora somewhere new.",
    "Time": "The time in the story.",
    "Weather": "The weather in the story.",
    "Goal": "What Lora is working toward, step by step.",
    "Just now": "The last thing that happened.",
    "Remembers": "Things Lora remembers from earlier.",
    "Found places": "New places Lora discovered.",
}

# What the Help chat knows. Plain and complete: it answers only from this.
GUIDE = """LIVE KINS: WHAT IT IS
A Windows app that gives a Kindroid character (a "kin") a day that moves on its own. A Narrator tells the story:
where Lora is, what Lora is doing, the weather, bedtime, and dreams. Kindroid is Lora's brain (Lora talks and has a
personality). DeepSeek writes what happens next. This app keeps time and changes the scene.

WORDS
- Kin: your AI character in Kindroid (here: Lora).
- Profile: who you are in a Kindroid chat. You switch it by tapping "Chatting as" under the message box.
- Narrator: a profile the app chats as, to tell the story. You make it in Kindroid and name it Narrator.
- Side character: an optional friend or pet (here: Mochi) who is always with Lora and talks every few minutes.
- Setting (current scene): the short line in Kindroid that says what Lora is doing now.

WHAT YOU NEED
A Windows computer and internet. A Kindroid account with a kin. A Kindroid API key (in Kindroid: Settings > General >
API & advanced integrations). A DeepSeek API key (platform.deepseek.com > API keys, with a little credit added).
Start.bat installs everything else (Python, the app's parts, and its browser) the first time.

SETUP (opens the first time, or Settings > App > Change setup)
Welcome: paste the Kindroid and DeepSeek keys. Find: press Find, log in to Kindroid in the window that opens, open
your kin's chat (and a group chat, if you use one), then close that window; your kins and profiles fill in by
themselves. Page 1: pick your kin, pronouns, and a few lines about them. Page 2: pick your own profile; the story
calls you by its name. Page 3 (optional): a side character: their name, what they are, their profile, and their kin
if they are one in a group chat. Page 4: pick the Narrator profile. Page 5 (optional): a group chat. Page 6: the
style of their world (modern, medieval, fantasy...), your time zone, and places (Suggest places fills them in).

THE MAIN WINDOW
- "What Lora is doing now": the current setting. Type one before the first Start (like: reading by the window).
- Start: begins the story. With the warning on, the Narrator warns Lora first and the change comes a minute later.
- Stop: stops everything. Sleep: Lora falls asleep; a dream comes every hour; Wake up ends it.
- Settings (top right): everything else. Help (bottom right): this chat. Back returns to Start, Stop, and Sleep.
- Leave it running and the story keeps going day after day. At Sleep at Lora falls asleep; at Wake at the new day
  starts. Press Stop to pause it; press Start to go on.

HOW A CHANGE WORKS
Every few minutes the Narrator warns Lora ("your surroundings will change in 1 minute"). Lora can answer:
  "Narrator extend time please" = stay longer (after 8 in a row the Narrator moves Lora somewhere new, except at night)
  "hey @Narrator ..." = do exactly that (like: hey @Narrator take me to the garden)
  "Narrator take me home" = go somewhere
  "Narrator change weather to rain" = new weather
  "Narrator make it night" = change something around Lora
If Lora doesn't answer, the change waits up to 2 minutes, then goes ahead. The Narrator then posts "before, ...
then, ... now, ..." in the chat and updates Lora's setting in Kindroid, then switches you back to your profile.
Once an hour in the day, the warning may mention a new place nearby. Lora decides whether to go.

SETTINGS
- Schedule: Change every (minutes, or 2h); Also at (extra times); Wake at; Sleep at (empty = never);
  "Each moment lasts as long as it would in real life" (on: the story picks real lengths like a 30-minute bath);
  "Warn Lora 1 minute before each change".
- Chat: use a group chat instead of the 1-on-1 chat; "set to Auto" if Kindroid picks who answers in the group;
  Kindroid site: v1 (the classic site, kindroid.ai/chat/...) or v2 (the new site, kindroid.ai/v2/chat/...), or
  "Not sure" to let the app check. It matters for the side character's wand. Setup asks it on the Find page.
- Side character (Mochi): talks every few minutes. In the 1-on-1 chat the app switches to their profile and presses
  Kindroid's suggest-message wand, then switches back. In a group chat, if they are a kin there, they take a turn.
  "Set up browser" only if they can't talk. Their AI ID is only for group chats.
- Narrator: the words Lora can say and what happens; the warning message ({where} and {wait} fill in); the words
  before each dream. Press Save to keep changes, Preview to see the message, Reset to default to undo.
- Right now: where Lora is, what Lora is doing, time, weather, goal, memories, and places Lora found.
- Places: one place per line, like "Kitchen with a stove, a table, and a teapot". Outdoor places say "outside".
  The story only uses places on this list (plus places Lora discovers).
- About (Lora): who Lora is. The story uses it to pick fitting things to do.
- Pictures: a slideshow behind the main window; Visible sets how strongly it shows.
- Theme: the app's colors. Ready-made themes: Default (purple), Midnight Ocean (blue), Pink Sakura (light pink),
  Fall Leaves (brown). Or pick your own: background, buttons and panels, text, soft text, boxes you type in,
  highlight (the lit button), and the kin's name. Click a color square to change it. The note under the squares
  says if something is hard to read. Colors change right away and are kept.
- mez.ink/ferisooo (bottom left): the maker's page. Click it to open it in the browser.
- Who made it: ferisooo, with help from Claude, an AI assistant by Anthropic. Anthropic is not connected to the app.
- Extras (each one off until ticked; they use the app's own browser that Find logged in, one at a time):
  Know Lora (reads backstory, key memories, directive from Kindroid each morning, or Read now; the story uses them),
  Journal (new places and a note about each day go into Lora's Kindroid journal: keyword memories that come back when
  the keyword comes up), Key memories (big moments added at the end of Lora's key memories; the rest is kept),
  Favorites (dreams and discoveries pinned), Mochi reacts (an emoji on Lora's message on Mochi's turn, new site),
  Selfies (Lora sends a selfie at new places; uses Kindroid credits; "Selfies a day" limits it), Background (the chat
  background shows Lora's latest selfie). If one can't be done (Kindroid changed its website), simulation.log says so.
- Legal: the Terms of Service and the Privacy Policy (also TERMS.txt and PRIVACY.txt in the app's folder).
  In short: the app is free and unofficial (not made by Kindroid or DeepSeek), used at your own risk, as is.
  It sends nothing to the maker. It only sends to Kindroid and DeepSeek, with your own keys, to make the story.
  Everything else stays on your computer. For anything legal, point to those two files; don't give legal advice.
- App: Change setup; Wipe everything (removes keys, names, login, story; say No to keeping the DeepSeek key before
  sharing the app); restart by itself after an update.

PROBLEMS
- "Your keys are missing": Settings > App > Change setup, paste the keys on the first page.
- "429 Too Many Requests": Kindroid is busy. Set Change every higher (like 20) and Talk every higher.
- Find shows nothing: in the Find window, log in and open your kin's chat, then press Find again.
- The side character doesn't talk: Settings > their panel > Set up browser, log in, close the window.
- Nothing happens after Start: type what Lora is doing in the box first. Check the keys. Check simulation.log.
- The Narrator's messages show up as you: Kindroid was busy switching back; it fixes itself at the next change.
- The app only works while it is open. Closing it pauses the story.
- simulation.log (in the app folder) lists everything the app did, with times.
"""

HELPER_RULES = """You are the helper inside the Live Kins app. The person asking may know nothing about
computers or Kindroid. Answer only from the guide below and the person's settings.
How to answer:
- Plain text only. No markdown: no stars, no bold, no headings, no tables.
- Short: about 60 words. Only what they asked. No extra notes or tips they did not ask for.
- Steps as a numbered list (1. 2. 3.), one short line each, and nothing after the list unless it is needed.
- Name buttons exactly as the guide does, in quotes only when it helps.
- If the guide does not say, say you are not sure and suggest simulation.log or Settings > App > Change setup.
- Never make up features. Answer in the language the person writes in."""
HISTORY_KEPT = 12  # earlier messages the helper still sees


def their_settings() -> str:
    """This person's names and settings, so the helper can answer about their app."""
    import cast
    import update_scene as us

    now = cast.cast()
    prefs = us.load_prefs()
    lines = [
        f"Their kin: {now['kin_name']} ({now['kin_pronoun']}). They chat as: {now['user_name']}. "
        f"Narrator profile: {now['narrator_name']}.",
        f"Side character: {now['chatter_name'] + ' (' + now['chatter_what'] + ')' if now['has_chatter'] else 'none'}.",
        f"World: {now.get('era') or 'not set'}. Time zone: {now.get('timezone') or 'not set'}.",
        f"Change every: {prefs.get('minutes') or '10'} minutes. Wake at: {prefs.get('wake') or '8:00 AM'}. "
        f"Sleep at: {prefs.get('sleep_at') or 'never'}.",
        f"Warning before changes: {'on' if str(prefs.get('heads_up', '1')) != '0' else 'off'}. "
        f"Group chat: {'on' if str(prefs.get('use_group') or '0') == '1' else 'off'}. "
        f"Side character talks: {'on' if str(prefs.get('wand') or '0') == '1' else 'off'}.",
    ]
    return "\n".join(lines)


def ask(history: list[dict]) -> str:
    """The helper's answer. history: {"role": "user" or "assistant", "content": ...}, oldest first."""
    import os

    import cast
    import update_scene as us

    us.load_env(us.ENV_PATH)
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("The helper needs your DeepSeek key. Add it in Settings > App > Change setup.")
    system = f"{HELPER_RULES}\n\nTHE GUIDE\n{cast.localize(GUIDE)}\n\nTHIS PERSON'S SETTINGS\n{their_settings()}"
    payload = {
        "model": "deepseek-flash",
        "messages": [{"role": "system", "content": system}, *history[-HISTORY_KEPT:]],
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
        "temperature": 0.3,
        "max_tokens": 600,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    result = us.post_json(us.DEEPSEEK_URL, payload, headers, timeout=60, required=True)
    try:
        return str(result["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise RuntimeError("DeepSeek sent an answer the helper could not read. Try again.") from error
