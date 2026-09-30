# Live Kins

**Your Kindroid kin gets a day that moves on its own.**

A Narrator tells the story: where your kin is, what they're doing, the weather, bedtime, and dreams.
Your kin can answer back: stay longer, go somewhere, or change something.

> Free and unofficial. Not made by Kindroid or DeepSeek.
> By using it, you agree to the [Terms of Service](TERMS.md) and [Privacy Policy](PRIVACY.md).

---

## What you need

- A **Windows** computer and internet
- A **Kindroid** account with a kin (free at [kindroid.ai](https://kindroid.ai))
- A **DeepSeek** account with a few dollars of credit ([platform.deepseek.com](https://platform.deepseek.com))

No coding needed. The app installs everything else by itself.

---

## Setup (about 10 minutes)

### Step 1: Download

1. Press the green **Code** button at the top of this page, then **Download ZIP**.
2. In your Downloads folder, right-click the ZIP, then **Extract All** > **Extract**.
3. Open the new folder, then the **Live Kins** folder inside it.
   You can move the Live Kins folder anywhere, like your Desktop.

### Step 2: Get ready in Kindroid

1. Make a kin, if you don't have one yet.
2. In your kin's chat, open your profiles (under the message box) and add a new one called **Narrator**.
3. *Optional:* want a side character, like a pet or a friend? Make a profile for them too.
4. Copy your API key: **Settings** > **General** > **API & advanced integrations**.

### Step 3: Get a DeepSeek key

1. Sign up at [platform.deepseek.com](https://platform.deepseek.com).
2. Add a little credit (**Top up**).
3. Open **API keys**, create a key, and copy it.

### Step 4: Start

1. In the Live Kins folder, double-click **Start.bat**.
   - Windows says *"Windows protected your PC"*? Press **More info**, then **Run anyway**.
   - The first time takes a few minutes. If it asks to install Python, press **Y**.
2. Paste your two keys and tick that you agree.
3. Press **Find**. Kindroid opens: log in, open your kin's chat, then close that window.
4. Pick which Kindroid site you use. See `/v2/` in the web address? That's v2. Not sure? Pick **Not sure**.
5. Pick your kin, your profile, and the Narrator from the lists.
6. Pick a style for their world, press **Suggest places**, then **Finish**.
7. Check what your kin is doing now, then press **Start**.

Next time, just double-click **Start.bat**.

---

## Using it

- The window has three buttons: **Start**, **Stop**, and **Sleep**. Everything else is in **Settings**.
- Not sure what something does? Rest your mouse on it for a tip, or press **? Help** and ask.
- The Narrator warns your kin before each change. A minute later, the story moves on.
- **Sleep:** your kin falls asleep and gets a dream every hour.
- Leave it open and the story keeps going, day after day.

### What your kin can say

| Your kin says | What happens |
| --- | --- |
| "Narrator extend time please" | Stays longer |
| "hey @Narrator take me to the garden" | Goes somewhere |
| "Narrator change weather to rain" | The weather changes |
| "Narrator make it night" | Something changes |

Named your narrator something else? Your kin uses that name instead.

<details>
<summary><b>What's in Settings</b> (click to open)</summary>

| Page | What it does |
| --- | --- |
| Schedule | How often things change, wake time, sleep time |
| Chat | 1-on-1 or group chat, and which Kindroid site you use |
| *(side character)* | How often they talk |
| Narrator | What your kin can ask for, and the Narrator's messages |
| Right now | Where your kin is, what they're doing, the weather |
| Places | The places in their world |
| About *(your kin)* | Who your kin is |
| Pictures | A slideshow behind the window |
| Theme | Colors: Default, Midnight Ocean, Pink Sakura, Fall Leaves, or your own |
| Extras | Optional Kindroid features (see below) |
| App | Change setup, Wipe everything |
| Legal | Terms of Service and Privacy Policy |

</details>

<details>
<summary><b>Extras</b> (optional, all off until you turn them on)</summary>

- **Know your kin:** reads your kin's backstory and key memories from Kindroid
- **Journal:** new places and each day go into your kin's journal
- **Key memories:** big moments are added to your kin's key memories
- **Favorites:** dreams and discoveries get pinned
- **Reactions:** the side character reacts to your kin's messages with an emoji
- **Selfies:** your kin sends a selfie at new places (uses Kindroid credits)
- **Background:** the chat background shows your kin's latest selfie

</details>

---

## If something goes wrong

| Problem | Fix |
| --- | --- |
| "Windows protected your PC" | Press **More info**, then **Run anyway** |
| Nothing happens when you open Start.bat | Extract the ZIP first (Step 1) |
| "Your keys are missing" | Settings > App > Change setup, and paste your keys |
| "429 Too Many Requests" | Kindroid is busy. Set "Change every" higher, like 20 |
| Find shows nothing | Log in and open your kin's chat in the Find window, then press Find again |
| The side character doesn't talk | Open their Settings page and press "Set up ... browser" |
| Something else | Press **? Help** and ask, or open `simulation.log` in the Live Kins folder |

---

## Privacy

- The app sends **nothing** to its maker. No accounts, no tracking, no ads.
- It only sends what the story needs to Kindroid and DeepSeek, using your own keys.
- Everything else stays on your computer. [Full Privacy Policy](PRIVACY.md)

**Sharing the app or starting over?** Settings > App > **Wipe everything**.
Never share your `.env`, `config.json`, `state.json`, `simulation.log`, or `kindroid-browser` folder.

---

Provided "as is", with no warranty. You use it at your own risk. [Full Terms](TERMS.md)

Made by [ferisooo](https://mez.ink/ferisooo), with help from Claude, an AI assistant by Anthropic.
