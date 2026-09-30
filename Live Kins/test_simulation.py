"""Checks for the persistent simulation. Nothing here reaches DeepSeek or Kindroid.

Run with: python -m unittest test_simulation -v
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import random
import logging
import re
import shutil
import tempfile
import time
import types
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import cast
import kindroid_finder
import setup_wizard
import narrator_commands
import simulation
import update_scene as us
import wand

ROOT = Path(__file__).resolve().parent
LA = us.LA
REAL_SWITCH = us.switch_profile
START = "lora lies on the rug by the fireplace, Mochi curled at her side, moonlight through the window and one last log glowing low."


def at(hour: int, minute: int = 0, day: int = 24) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=LA)


def plan(location: str, setting: str, **extra) -> dict:
    data = {
        "location": location,
        "activity": setting.split(",")[0],
        "mochi_activity": "close by",
        "objects": [],
        "weather": "clear",
        "lights": "",
        "goal_step_done": False,
        "new_goal": None,
        "event": setting,
        "memory": None,
        "bridge": "lora finished what she was doing and walked over with Mochi.",
        "setting": setting,
    }
    data.update(extra)
    return data


class Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record) -> None:
        self.lines.append(record.getMessage())


class Fakes:
    """Stand-ins for DeepSeek and Kindroid."""

    def __init__(self) -> None:
        self.plans: list = []
        self.prompts: list[str] = []
        self.fits = True
        self.legacy = RuntimeError("DeepSeek is down")
        self.dream = (
            "You are standing in the sunflower field, but the flowers are taller than the house and humming softly.\n\n"
            "Mochi trots ahead on a path made of warm biscuits, and every step leaves a little glowing pawprint "
            "that floats up like a lantern. The sky is the color of strawberry milk, and somewhere far away "
            "you can hear Master laughing, though you cannot see him yet. The air smells of rain and cinnamon."
        )
        self.sent: list[tuple[str, str]] = []
        self.profiles: list[str] = []
        self.fail_tell = False
        self.chat: list[dict] = []

    def ask_json(self, key, prompt, system, **kwargs):
        self.prompts.append(prompt)
        item = self.plans.pop(0) if self.plans else ValueError("no plan queued")
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)

    def ask(self, key, prompt, system=None, max_tokens=120, temperature=1.1, paragraph=False, raw=False):
        if raw:
            self.prompts.append(prompt)
            if isinstance(self.dream, Exception):
                raise self.dream
            return self.dream
        if paragraph:
            return "yes" if self.fits else "no"
        if isinstance(self.legacy, Exception):
            raise self.legacy
        return self.legacy

    def switch(self, key, profile):
        self.profiles.append(str(profile.get("user_name")))

    def save(self, key, ai_id, scene):
        self.sent.append(("setting", scene))

    def tell(self, key, ai_id, message):
        if self.fail_tell:
            raise RuntimeError("https://api.kindroid.ai/v1/send-message returned 500: busy")
        self.sent.append(("message", message))
        return "ok"

    def fetch(self, after, group_id=None):
        return self.chat, 0


TEST_CONFIG = {
    "master_profile": {"id": "master-id", "user_name": "Master"},
    "narrator_profile": {"id": "narrator-id", "user_name": "Narrator"},
    "mochi_profile": {"id": "mochi-id", "user_name": "Mochi"},
}
TEST_MAP = {"connections": [{"from": "zone:bedroom", "to": "zone:outdoor", "via": "sliding glass doors"}], "zones": {}}


class MedievalWorldTest(unittest.TestCase):
    """The 500 AD Environment list reads as real places, and the planner is told the era."""

    def test_the_list_reads_cleanly(self) -> None:
        path = ROOT / "environment_500ad.txt"
        if not path.exists():
            self.skipTest("no environment_500ad.txt")
        catalog = simulation.build_catalog(path.read_text(encoding="utf-8"))
        places = catalog["places"]
        self.assertGreaterEqual(len(places), 100)
        self.assertEqual(places["bedroom"]["zone"], "bedroom")
        self.assertEqual(places["open-field"]["zone"], "outdoor")
        self.assertEqual(places["great-hall"]["zone"], "indoor")
        self.assertEqual(places["old-roman-ruins"]["zone"], "outdoor")
        self.assertTrue(all(place["items"] for place in places.values()))
        self.assertIn("snow", catalog["weather"])

    def test_the_planner_is_told_the_era(self) -> None:
        folder = Path(tempfile.mkdtemp())
        config = folder / "config.json"
        config.write_text(json.dumps({"cast": {"era": "Medieval, around 500 AD"}}), encoding="utf-8")
        with mock.patch.object(cast, "CONFIG_PATH", config):
            cast.reset_cache()
            for late in (True, False):
                self.assertIn("around 500 AD", us.change_rules_for(late, ""))
            config.write_text(json.dumps({"cast": {"era": ""}}), encoding="utf-8")
            cast.reset_cache()
            self.assertNotIn("500 AD", us.change_rules_for(False, ""))
        cast.reset_cache()
        shutil.rmtree(folder, ignore_errors=True)
        self.assertNotIn("shoerack", us.change_rules_for(True, ""))
        self.assertNotIn("chandelier", us.change_rules_for(False, ""))

    def test_the_house_has_a_front_door(self) -> None:
        links = json.loads((ROOT / "world_map.json").read_text(encoding="utf-8"))
        catalog = simulation.build_catalog((ROOT / "environment_500ad.txt").read_text(encoding="utf-8"))
        self.assertTrue(simulation.can_move(catalog, links, "kitchen-area", "courtyard")[0])
        self.assertTrue(simulation.can_move(catalog, links, "bedroom", "orchard")[0])


CUSTOM_CAST = {
    "kin_name": "Aria", "kin_pronoun": "he", "user_name": "Jay", "chatter_name": "Biscuit",
    "chatter_what": "orange cat", "has_chatter": True, "narrator_name": "Storyteller", "era": "Cozy fantasy",
    "timezone": "Europe/London",
}


class CastTest(unittest.TestCase):
    """Someone else's names everywhere: their kin, themselves, their side chatter, their narrator."""

    def use(self, **changes) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.config = self.folder / "config.json"
        data = {
            "cast": dict(CUSTOM_CAST, **changes),
            "master_profile": {"id": "j1", "user_name": "Jay"},
            "narrator_profile": {"id": "s1", "user_name": "Storyteller"},
            "mochi_profile": {"id": "b1", "user_name": "Biscuit"},
        }
        self.config.write_text(json.dumps(data), encoding="utf-8")
        self.patches = [mock.patch.object(cast, "CONFIG_PATH", self.config), mock.patch.object(us, "CONFIG_PATH", self.config)]
        for patch in self.patches:
            patch.start()
        cast.reset_cache()

    def tearDown(self) -> None:
        for patch in getattr(self, "patches", []):
            patch.stop()
        cast.reset_cache()
        shutil.rmtree(getattr(self, "folder", Path("/nonexistent")), ignore_errors=True)

    def test_example_names_change_nothing(self) -> None:
        text = "*heads up, lora: Mochi and Master. say hey @narrator*"
        with mock.patch.object(cast, "CONFIG_PATH", Path(tempfile.gettempdir()) / "no-such-config.json"):
            cast.reset_cache()
            self.assertEqual(cast.localize(text), text)

    def test_their_names_everywhere(self) -> None:
        self.use()
        self.assertFalse(cast.needs_setup())
        text = cast.localize(us.heads_up_message("1 minute", "at the kitchen", "20 minutes", 3))
        self.assertIn("*heads up, aria:", text)
        self.assertIn('say "Storyteller extend time please"', text)
        self.assertIn('say "hey @storyteller"', text)
        self.assertNotIn("lora", text.lower())
        self.assertNotIn("narrator", text.lower())
        self.assertEqual(
            cast.pronouns(cast.localize("lora stays where she is, Mochi curled up beside her. Master loves her book.")),
            "aria stays where he is, Biscuit curled up beside him. Jay loves his book.",
        )
        self.assertIn("orange cat", cast.localize("Mochi, her white Bichon, is with her"))
        self.assertIn("aria uses he/him/his pronouns".lower(), cast.pronoun_note().lower())
        self.assertIn("gentle fantasy realm", us.change_rules_for(False, ""))
        self.assertEqual(str(us.zone()), "Europe/London")

    def test_commands_use_the_narrator_name(self) -> None:
        self.use()

        def said(text: str, name: str = "Aria") -> tuple:
            return us.narrator_command_from([{"message": text, "sender": "ai", "display_name": name, "timestamp": 5}])[:3]

        self.assertEqual(said("Storyteller extend time please."), ("extend", "Aria", ""))
        self.assertEqual(said("hey @storyteller take me to the kitchen"), ("go", "Aria", "take me to the kitchen"))
        self.assertEqual(said("Storyteller, take me home."), ("go", "Aria", "take me home"))
        self.assertEqual(said("storyteller change weather to snow"), ("weather", "Aria", "snow"))
        self.assertEqual(said("hey @narrator take me outside"), ("go", "Aria", "take me outside"))
        self.assertEqual(said("Storyteller extend time please", name="Storyteller"), ("", "", ""))
        self.assertEqual(said("hey @storyteller take me to the kitchen", name="")[1], "Aria")

    def test_their_own_commands_use_the_narrator_name(self) -> None:
        self.use()
        state = self.folder / "state.json"
        self.patches.append(mock.patch.object(us, "STATE_PATH", state))
        self.patches[-1].start()
        found = narrator_commands.defaults()
        self.assertEqual(found["commands"][0]["say"], "Storyteller extend time please")
        self.assertTrue(found["message"].startswith("heads up, aria: {where}your surroundings will change automatically in {wait}.\n"))
        story = {"say": "Storyteller tell me a story", "does": "custom", "what": "Jay tells Aria a story"}
        found["commands"].append(story)
        found["message"] = narrator_commands.with_line_for(found["message"], story)
        # Deleting the default weather command takes out its line, in the setup's names too.
        weather = found["commands"][2]
        self.assertEqual(weather["say"], "storyteller change weather to")
        self.assertNotIn("change the weather", narrator_commands.without_lines_of(found["message"], weather))
        self.assertEqual(narrator_commands.problems(found), [])
        self.assertTrue(narrator_commands.problems({"commands": [{"say": "Storyteller", "does": "change"}]}))
        us.save_prefs({"narrator": found})

        def said(text: str) -> tuple:
            return us.narrator_command_from([{"message": text, "sender": "ai", "display_name": "Aria", "timestamp": 5}])[:3]

        self.assertEqual(said("storyteller, tell me a story!"), ("go", "Aria", "Jay tells Aria a story"))
        self.assertEqual(said("Storyteller extend time please"), ("extend", "Aria", ""))
        message = us.heads_up_message("1 minute", "at the kitchen", "20 minutes")
        self.assertTrue(message.startswith("*heads up, aria: you've been at the kitchen"))
        self.assertIn('you can also say "Storyteller tell me a story".', message)

    def test_the_side_chatter_is_checked_by_name(self) -> None:
        self.use()
        self.assertTrue(us.has_mochi("aria reads by the fire, Biscuit asleep in her lap"))
        self.assertFalse(us.has_mochi("aria reads by the fire alone"))

    def test_no_side_chatter(self) -> None:
        self.use(has_chatter=False, chatter_name="", chatter_what="")
        self.assertTrue(us.has_mochi("aria reads by the fire alone"))
        self.assertEqual(cast.localize("lora fast asleep in bed, Mochi curled up beside her, dreaming"), "aria fast asleep in bed, dreaming")
        prompt = cast.localize("- Mochi goes wherever she goes.\n- A different place.")
        self.assertEqual(prompt.strip(), "- A different place.")

    def test_messages_to_kindroid_carry_their_names(self) -> None:
        self.use()
        sent = []
        with mock.patch.object(us, "request_body", lambda url, body, headers, timeout: sent.append(body) or "ok"), \
                mock.patch.object(us, "post_json", lambda url, body, headers, timeout=30, required=False: sent.append(body)):
            us.tell_lora("k", "ai", "*lora stays where she is, Mochi beside her*")
            us.save_kindroid("k", "ai", "lora by the fire, Mochi asleep")
        self.assertEqual(sent[0]["message"], "*aria stays where she is, Biscuit beside her*")
        self.assertEqual(sent[1]["current_scene"], "aria by the fire, Biscuit asleep")


def setup_answers(**changes) -> dict:
    found = {
        "agree": True, "kindroid_key": "kk", "deepseek_key": "dk", "ai_id": "ai1", "kin_name": "Aria", "kin_pronoun": "she",
        "about_kin": "Aria loves the sea.", "you_name": "Jay", "you_id": "j1", "you_gender": "Male", "you_backstory": "",
        "has_chatter": True, "chatter_name": "Biscuit", "chatter_what": "orange cat", "chatter_id": "b1",
        "chatter_gender": "", "chatter_backstory": "", "narrator_name": "Storyteller", "narrator_id": "s1",
        "narrator_backstory": "I tell the story.", "era": "Cozy fantasy", "timezone": "Europe/London",
        "environment": "Bedroom with a bed and a lamp.\nKitchen with a stove and a table.\nGarden outside with roses and a bench.",
    }
    found.update(changes)
    return found


class SetupTest(unittest.TestCase):
    """What the setup window saves, checked without the window."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(cast, "CONFIG_PATH", self.folder / "config.json"),
            mock.patch.object(us, "CONFIG_PATH", self.folder / "config.json"),
            mock.patch.object(us, "ENV_PATH", self.folder / ".env"),
            mock.patch.object(us, "STATE_PATH", self.folder / "state.json"),
            mock.patch.object(us, "BACKUP_PATH", self.folder / "state.backup.json"),
        ]
        for patch in self.patches:
            patch.start()
        (self.folder / "config.json").write_text(json.dumps({"master_profile": {"id": "", "user_name": ""}}), encoding="utf-8")
        cast.reset_cache()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        cast.reset_cache()
        shutil.rmtree(self.folder, ignore_errors=True)

    def answers(self, **changes) -> dict:
        return setup_answers(**changes)

    def test_a_new_copy_asks_for_setup(self) -> None:
        self.assertTrue(cast.needs_setup())

    def test_missing_answers_are_named(self) -> None:
        import setup_wizard

        self.assertEqual(setup_wizard.check_answers(self.answers()), "")
        self.assertIn("the Narrator's profile (page 4)", setup_wizard.check_answers(self.answers(narrator_id="")))
        self.assertIn("the side character's name", setup_wizard.check_answers(self.answers(chatter_name="")))
        self.assertEqual(setup_wizard.check_answers(self.answers(has_chatter=False, chatter_name="", chatter_id="")), "")
        self.assertIn("at least 3 places", setup_wizard.check_answers(self.answers(environment="Bedroom with a bed.")))
        self.assertIn("different name", setup_wizard.check_answers(self.answers(narrator_name="Aria")))

    def test_setup_saves_keys_profiles_names_and_world(self) -> None:
        import setup_wizard

        setup_wizard.save_setup(self.answers())
        env = (self.folder / ".env").read_text(encoding="utf-8")
        self.assertIn("KINDROID_AI_ID=ai1", env)
        self.assertIn("DEEPSEEK_API_KEY=dk", env)
        config = json.loads((self.folder / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["narrator_profile"], {"id": "s1", "user_name": "Storyteller", "user_backstory": "I tell the story."})
        self.assertEqual(config["master_profile"], {"id": "j1", "user_name": "Jay", "user_gender": "Male"})
        self.assertEqual(config["mochi_profile"]["id"], "b1")
        self.assertEqual(config["cast"]["kin_name"], "Aria")
        self.assertFalse(cast.needs_setup())
        self.assertEqual(cast.cast()["narrator_name"], "Storyteller")
        prefs = us.load_prefs()
        self.assertIn("Kitchen with a stove", prefs["environment"])
        self.assertEqual(prefs["backstory"], "Aria loves the sea.")
        # Again without a side chatter: it is taken out.
        setup_wizard.save_setup(self.answers(has_chatter=False, chatter_name="", chatter_id="", chatter_what=""))
        config = json.loads((self.folder / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("mochi_profile", config)
        self.assertFalse(cast.has_chatter())
        self.assertEqual(us.load_prefs()["wand"], "0")

    def test_changing_names_in_setup_changes_the_saved_narrator_texts(self) -> None:
        setup_wizard.save_setup(self.answers())
        self.assertEqual(us.load_prefs()["last_greet_date"], us.la_now().date().isoformat())  # no start by itself
        us.save_prefs(dict(us.load_prefs(), narrator={
            "message": "heads up, aria: say \"Storyteller extend time please\". Biscuit waits. Aria's turn.",
            "dream": "aria, you are dreaming",
            "commands": [{"say": "Storyteller extend time please", "does": "stay"}],
        }))
        setup_wizard.save_setup(self.answers(kin_name="Ron", narrator_name="Sage"))
        saved = us.load_prefs()["narrator"]
        self.assertEqual(saved["message"], "heads up, ron: say \"Sage extend time please\". Biscuit waits. Ron's turn.")
        self.assertEqual(saved["dream"], "ron, you are dreaming")
        self.assertEqual(saved["commands"], [{"say": "Sage extend time please", "does": "stay"}])

    def test_suggested_places_are_lines_the_app_can_read(self) -> None:
        reply = (
            "1. Tide pool outside with crabs, starfish, and seaweed.\n"
            "- Lighthouse outside with a spiral stair, a lamp room, and gulls\n"
            "Here are some places:\n"
            "Kitchen with a stove and a table.\n"
            "Boathouse with oars, a rowboat, and nets."
        )
        with mock.patch.object(us, "ask_deepseek", lambda *args, **kwargs: reply):
            lines = simulation.suggest_places("Aria loves the sea", "Cozy fantasy", "Kitchen with a stove and a table.", key="dk")
        self.assertEqual(
            lines,
            [
                "Tide pool outside with crabs, starfish, and seaweed.",
                "Lighthouse outside with a spiral stair, a lamp room, and gulls.",
                "Boathouse with oars, a rowboat, and nets.",
            ],
        )

    def test_a_first_line_without_a_bedroom_is_read_normally(self) -> None:
        catalog = simulation.build_catalog("Kitchen with a stove, a sink, and a table.\nGarden outside with roses and a bench.")
        self.assertEqual({key: place["zone"] for key, place in catalog["places"].items()}, {"kitchen": "indoor", "garden": "outdoor"})

    def test_the_setup_window_walks_through_and_saves(self) -> None:
        import setup_wizard

        wizard = setup_wizard.SetupWizard()
        wizard.withdraw()
        try:
            answers = self.answers()
            for key, var in wizard.values.items():
                if key in answers:
                    var.set(answers[key])
            wizard.values["era_choice"].set("Cozy fantasy")
            for key, box in wizard.texts.items():
                box.delete("1.0", "end")
                box.insert("1.0", answers.get(key, ""))
            self.assertEqual(wizard.page, 0)
            for _ in range(7):
                wizard.next()
            self.assertEqual(wizard.page, 7)
            wizard.values["narrator_id"].set("")
            wizard.next()
            self.assertIn("Narrator's profile (page 4)", wizard.message.cget("text"))
            wizard.values["narrator_id"].set("s1")
            wizard.next()
            self.assertTrue(wizard.finished)
        finally:
            try:
                wizard.destroy()
            except tk.TclError:
                pass
        self.assertEqual(cast.cast()["kin_name"], "Aria")
        self.assertEqual(cast.cast()["era"], "Cozy fantasy")


class FinderTest(unittest.TestCase):
    """Setup's Find: what it reads while you click around Kindroid, and how setup lists it to pick from."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(cast, "CONFIG_PATH", self.folder / "config.json"),
            mock.patch.object(us, "CONFIG_PATH", self.folder / "config.json"),
            mock.patch.object(us, "ENV_PATH", self.folder / ".env"),
            mock.patch.object(us, "STATE_PATH", self.folder / "state.json"),
            mock.patch.object(us, "BACKUP_PATH", self.folder / "state.backup.json"),
            mock.patch.object(wand, "PROFILE", self.folder / "browser"),
        ]
        for patch in self.patches:
            patch.start()
        (self.folder / "config.json").write_text("{}", encoding="utf-8")
        cast.reset_cache()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        cast.reset_cache()
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_ids_are_read_from_kindroid_addresses_and_requests(self) -> None:
        self.assertEqual(kindroid_finder.kin_id_in("https://kindroid.ai/chat/MainKinAiId000000005/"), "MainKinAiId000000005")
        self.assertEqual(kindroid_finder.kin_id_in("https://kindroid.ai/v2/chat/SideKinAiId000000003/"), "SideKinAiId000000003")
        self.assertEqual(kindroid_finder.kin_id_in("https://kindroid.ai/home"), "")
        self.assertEqual(kindroid_finder.group_id_in("https://kindroid.ai/groupchat/GroupChatId000000004"), "GroupChatId000000004")
        self.assertEqual(kindroid_finder.group_id_in('{"group_id": "GroupChatId000000004"}'), "GroupChatId000000004")
        self.assertEqual(kindroid_finder.PERSONA.search('{"active_persona_id":"YouProfile0000000001"}').group(1), "YouProfile0000000001")

    def test_a_kins_name_is_read_next_to_their_picture(self) -> None:
        if not have_browser():
            self.skipTest("Playwright is not installed")
        from playwright.sync_api import sync_playwright

        page_html = """<div><a>Profile</a><a>Chat</a></div><div><span>Kins</span><span>Groups</span></div>
            <div><div><img src="https://storage.kindroid.ai/users/u1/OtherKinAiId00000006/a.jpg"></div><p>Feris</p><b>...</b></div>
            <div><div><img src="https://storage.kindroid.ai/users/u1/SideKinAiId000000003/a.jpg"></div><p>Mochi</p></div>"""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(page_html)
            finder = kindroid_finder.Finder()
            self.assertEqual(finder._page_name([page], "SideKinAiId000000003"), "Mochi")
            self.assertEqual(finder._page_name([page], "OtherKinAiId00000006"), "Feris")
            self.assertEqual(finder._page_name([page], "notOnThePage1"), "")
            browser.close()

    def test_profiles_are_read_from_what_kindroids_database_sends(self) -> None:
        stream = (
            '123\n[[4,[{"documentChange":{"document":{"name":"projects/kindroid-ai/databases/(default)/documents/'
            'Users/u1/UserPersonas/YouProfile0000000001","fields":{"createdAt":{"integerValue":"1"},'
            '"name":{"stringValue":"Jay"}}}}},{"documentChange":{"document":{"name":"projects/kindroid-ai/databases/'
            '(default)/documents/Users/u1/UserPersonas/NarratorProfile00002","fields":{"user_name":'
            '{"stringValue":"Sto\\u00e9ry"}}}}}]]]'
        )
        self.assertEqual(
            kindroid_finder.personas_in(stream), {"YouProfile0000000001": "Jay", "NarratorProfile00002": "Stoéry"}
        )
        self.assertEqual(kindroid_finder.personas_in("nothing here"), {})

    def test_the_page_is_listened_along_for_profiles_and_the_open_kins_name(self) -> None:
        if not have_browser():
            self.skipTest("Playwright is not installed")
        from playwright.sync_api import sync_playwright

        stream = ('[[4,[{"documentChange":{"document":{"name":"projects/kindroid-ai/databases/(default)/documents/'
                  'Users/u1/UserPersonas/YouProfile0000000001","fields":{"name":{"stringValue":"Jay"}}}}}]]]')
        chat = """<button aria-label="Open feris">feris</button>
            <button aria-label="Open Mochi" aria-current="page">Mochi</button>
            <script>
            const xhr = new XMLHttpRequest();
            xhr.open('GET', 'https://firestore.googleapis.com/google.firestore.v1.Firestore/Listen/channel?x=1');
            xhr.send();
            </script>"""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            context.add_init_script(kindroid_finder.LISTEN_ALONG)
            context.route("https://kindroid.ai/**", lambda route: route.fulfill(body=chat, content_type="text/html"))
            context.route(
                "https://firestore.googleapis.com/**",
                lambda route: route.fulfill(body=stream, headers={"Access-Control-Allow-Origin": "*"}),
            )
            page = context.new_page()
            page.goto("https://kindroid.ai/v2/chat/SideKinAiId000000003/")
            page.wait_for_function("window.__kindroidFinder.length > 0", timeout=5000)
            finder = kindroid_finder.Finder()
            finder._read_profiles([page])
            self.assertEqual(finder.found()["profiles"], {"YouProfile0000000001": "Jay"})
            self.assertEqual(finder._page_name([page], "SideKinAiId000000003"), "Mochi")
            browser.close()

    def test_the_kins_setting_and_backstory_come_from_kindroid(self) -> None:
        stream = (
            '[[5,[{"documentChange":{"document":{"name":"projects/kindroid-ai/databases/(default)/documents/Users/u1/AIs/'
            'ai1xxxxxxxx","fields":{"name":{"stringValue":"Aria"},"current_scene":{"stringValue":"aria reading by the window"},'
            '"backstory":{"stringValue":"Aria loves the sea."}}}}}]]]'
        )
        details = kindroid_finder.kin_details(kindroid_finder.kins_in(stream)["ai1xxxxxxxx"])
        self.assertEqual(details, {"name": "Aria", "scene": "aria reading by the window", "backstory": "Aria loves the sea."})
        self.assertEqual(kindroid_finder.kin_details({"voice": "x"}), {"name": "", "scene": "", "backstory": ""})
        # Setup: the story starts from the kin's setting. No setting: it stays empty.
        found = {"kins": {"ai1": {"name": "Aria", "url": "", "scene": "aria reading by the window, " + "x " * 100}}}
        setup_wizard.save_setup(setup_answers(), found)
        self.assertTrue(us.saved_prior().startswith("aria reading by the window"))
        self.assertLessEqual(len(us.saved_prior()), us.SCENE_LIMIT)
        # Another kin, with no setting in Kindroid: a new, empty story.
        setup_wizard.save_setup(setup_answers(ai_id="ai2"), {"kins": {"ai2": {"name": "Ron", "url": ""}}})
        self.assertEqual(us.read_state().get("history"), [])
        # Picking a kin fills About them with their backstory, only when it is empty.
        wizard = setup_wizard.SetupWizard()
        wizard.withdraw()
        try:
            wizard.found = {"kins": {"ai1": {"name": "Aria", "url": "", "backstory": "Aria loves the sea."}}, "profiles": {}, "groups": {}}
            wizard.texts["about_kin"].delete("1.0", "end")
            wizard._kin_picked("ai1")
            self.assertEqual(wizard.texts["about_kin"].get("1.0", "end").strip(), "Aria loves the sea.")
            wizard._kin_picked("ai1")
            self.assertEqual(wizard.texts["about_kin"].get("1.0", "end").strip(), "Aria loves the sea.")
        finally:
            wizard.destroy()

    def test_setup_remembers_what_find_saw(self) -> None:
        self.assertEqual(setup_wizard.load_found(), {"kins": {}, "profiles": {}, "groups": {}})
        seen = {"kins": {"aria1": {"name": "Aria", "url": ""}}, "profiles": {"jay1": "Jay"}, "groups": {}}
        setup_wizard.save_found(seen)
        self.assertEqual(setup_wizard.load_found(), seen)

    def test_a_group_is_checked_and_its_kins_are_found(self) -> None:
        lines = [
            {"timestamp": 1, "sender": "user", "display_name": "Jay", "message": "hi"},
            {"timestamp": 2, "sender": "ai", "display_name": "Aria", "ai_id": "aria1", "message": "hello"},
            {"timestamp": 3, "sender": "ai", "display_name": "Biscuit", "ai_id": "biscuit1", "message": "mrrp"},
        ]
        asked = []

        def fetch(after, group_id=None, kin_id="", api_key=""):
            asked.append((group_id, kin_id, api_key))
            return lines, 3

        finder = kindroid_finder.Finder(api_key="kk")
        with mock.patch.object(us, "fetch_messages", fetch):
            finder._check_group("grp1")
        found = finder.found()
        self.assertTrue(found["groups"]["grp1"]["ok"])
        self.assertEqual(found["groups"]["grp1"]["kins"], {"aria1": "Aria", "biscuit1": "Biscuit"})
        self.assertEqual(found["kins"]["biscuit1"]["name"], "Biscuit")
        self.assertEqual(asked[0], ("grp1", "", "kk"))
        # Without the key it cannot be checked, and setup does not offer it.
        unkeyed = kindroid_finder.Finder()
        unkeyed._check_group("grp2")
        self.assertIn("API key", unkeyed.found()["groups"]["grp2"]["problem"])
        self.assertEqual(setup_wizard.group_choices(unkeyed.found()), {})

    def test_setup_lists_what_was_found(self) -> None:
        found = {
            "kins": {"aria1": {"name": "Aria", "url": ""}, "x2345678901": {"name": "", "url": ""}},
            "profiles": {"jay1": "Jay", "sto1": "Storyteller"},
            "groups": {"grp1": {"ok": True, "problem": "", "kins": {"aria1": "Aria", "biscuit1": "Biscuit"}}},
        }
        kins = setup_wizard.kin_choices(found)
        self.assertEqual(kins["Aria  (AI ID aria1)"], ("aria1", "Aria"))
        self.assertIn(("x2345678901", ""), kins.values())
        self.assertEqual(setup_wizard.profile_choices(found)["Storyteller  (ID sto1)"], ("sto1", "Storyteller"))
        self.assertEqual(list(setup_wizard.group_choices(found).values()), [("grp1", "Aria, Biscuit")])

    def test_picking_from_the_lists_fills_in_ids_and_names(self) -> None:
        wizard = setup_wizard.SetupWizard()
        wizard.withdraw()
        try:
            wizard.found = {
                "kins": {"aria1": {"name": "Aria", "url": "https://kindroid.ai/chat/aria1/"}},
                "profiles": {"jay1": "Jay"},
                "groups": {"grp1": {"ok": True, "problem": "", "kins": {"aria1": "Aria", "biscuit1": "Biscuit"}}},
            }
            wizard._refresh_pickers()
            box, _kind, _key = next(item for item in wizard.pickers if item[2] == "ai_id")
            box.set("Aria  (AI ID aria1)")
            box.event_generate("<<ComboboxSelected>>")
            wizard.update()
            self.assertEqual(wizard.values["ai_id"].get(), "aria1")
            self.assertEqual(wizard.values["kin_name"].get(), "Aria")
            # The side chatter's kin comes from the group's lines when it has their name.
            wizard.values["has_chatter"].set(True)
            wizard.values["chatter_name"].set("Biscuit")
            wizard.values["group_id"].set("grp1")
            self.assertEqual(wizard.answers()["chatter_ai_id"], "biscuit1")
        finally:
            wizard.destroy()

    def test_setup_saves_the_group_and_the_wand_chat(self) -> None:
        answers = setup_answers(use_group=True, group_id="grp1", chatter_ai_id="biscuit1")
        self.assertEqual(setup_wizard.check_answers(answers), "")
        self.assertIn("group chat", setup_wizard.check_answers(dict(answers, group_id="")))
        self.assertIn("own profile", setup_wizard.check_answers(dict(answers, narrator_id="j1")))
        found = {"kins": {"ai1": {"name": "Aria", "url": "https://kindroid.ai/chat/ai1/"}}}
        setup_wizard.save_setup(answers, found)
        config = json.loads((self.folder / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["group_id"], config["mochi_ai_id"]), ("grp1", "biscuit1"))
        self.assertEqual(us.load_prefs()["use_group"], "1")
        self.assertEqual(us.active_group(), "grp1")
        self.assertEqual(wand.saved_chat(), "https://kindroid.ai/chat/ai1/")
        # No group ticked: the 1-on-1 chat.
        setup_wizard.save_setup(dict(answers, use_group=False), found)
        self.assertEqual(us.active_group(), "")


class GroupChatTest(unittest.TestCase):
    """With "Use the group chat" on, the Narrator talks in the group and asks the kin (and sometimes the side chatter)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "state.json").write_text(json.dumps({"prefs": {"use_group": "1"}}), encoding="utf-8")
        (self.tmp / "config.json").write_text(json.dumps(
            {"group_id": "grp1", "mochi_ai_id": "mochi1", "mochi_profile": {"user_name": "Mochi"}}
        ), encoding="utf-8")
        self.calls: list[tuple[str, dict]] = []

        def body(url, payload, headers, timeout):
            self.calls.append((url.rsplit("/", 1)[-1], payload))
            if url.endswith("groupchats-ai-response"):
                return json.dumps({"reply": f"hi from {payload['ai_id']}"})
            if url.endswith("send-message"):
                return "hi from the 1-on-1"
            return "{}"

        self.patches = [
            mock.patch.object(us, "STATE_PATH", self.tmp / "state.json"),
            mock.patch.object(us, "BACKUP_PATH", self.tmp / "state.backup.json"),
            mock.patch.object(us, "CONFIG_PATH", self.tmp / "config.json"),
            mock.patch.object(cast, "CONFIG_PATH", self.tmp / "config.json"),
            mock.patch.object(us, "request_body", body),
            mock.patch.object(us, "la_now", lambda: at(14)),
            mock.patch.object(us, "load_env", lambda path: None),
            mock.patch.object(us, "require_keys", lambda: ("deepseek", "kindroid", "lora1")),
            mock.patch.object(us, "GROUP_WAIT_SECONDS", 0),
            mock.patch.object(us, "GROUP_CHECK_SECONDS", 0),
            mock.patch.object(us, "recent_messages", lambda after, pages=3: list(self.chat)),
        ]
        self.chat: list[dict] = []
        for patch in self.patches:
            patch.start()
        cast.reset_cache()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        cast.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self) -> list[str]:
        return [name for name, _payload in self.calls]

    def test_when_the_website_already_made_lora_answer_the_app_does_not_ask_again(self) -> None:
        us.save_prefs(dict(us.load_prefs(), group_auto="1"))
        self.chat = [
            {"timestamp": 1, "sender": "user", "display_name": "Narrator", "message": "*heads up, lora*"},
            {"timestamp": 2, "sender": "ai", "display_name": "Mochi", "message": "Ruff!"},
            {"timestamp": 3, "sender": "ai", "display_name": "lora", "message": "Narrator extend time please."},
        ]
        with mock.patch.object(us.random, "random", lambda: 0.1):
            reply = us.tell_lora("kindroid", "lora1", "*heads up, lora*")
        self.assertEqual(reply, "Narrator extend time please.")
        self.assertEqual(self.names(), ["groupchats-user-message"])

    def test_a_busy_kindroid_after_posting_never_posts_the_message_twice(self) -> None:
        real = us.request_body

        def busy(url, payload, headers, timeout):
            if url.endswith("groupchats-ai-response"):
                self.calls.append(("groupchats-ai-response", payload))
                raise RuntimeError(f"{url} returned 429: Too Many Requests (API limit)")
            return real(url, payload, headers, timeout)

        with mock.patch.object(us, "request_body", busy):
            reply = us.tell_lora("kindroid", "lora1", "*heads up, lora*")
        self.assertEqual(reply, "")
        self.assertEqual(self.names(), ["groupchats-user-message", "groupchats-ai-response"])

    def test_you_are_switched_back_right_after_the_narrator_posts(self) -> None:
        config = json.loads((self.tmp / "config.json").read_text(encoding="utf-8"))
        config["narrator_profile"] = {"id": "n1", "user_name": "Narrator"}
        (self.tmp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        order = []
        with mock.patch.object(us, "chatter_profile", lambda config: {"id": "m1", "user_name": "Master"}), \
                mock.patch.object(us, "switch_profile", lambda key, profile: order.append(("switch", profile["user_name"]))), \
                mock.patch.object(us.random, "random", lambda: 0.9):
            real_body = us.request_body

            def body(url, payload, headers, timeout):
                order.append(url.rsplit("/", 1)[-1])
                return real_body(url, payload, headers, timeout)

            with mock.patch.object(us, "request_body", body):
                us.narrator_says("*heads up*")
        self.assertEqual(order[:4], [("switch", "Narrator"), "groupchats-user-message", ("switch", "Master"), "groupchats-ai-response"])

    def test_the_narrator_talks_in_the_group(self) -> None:
        with mock.patch.object(us.random, "random", lambda: 0.1):
            reply = us.tell_lora("kindroid", "lora1", "*heads up, lora*")
        self.assertEqual(reply, "hi from lora1")
        self.assertEqual(self.names(), ["groupchats-user-message", "groupchats-ai-response", "groupchats-ai-response"])
        self.assertEqual(self.calls[0][1], {"group_id": "grp1", "message": "*heads up, lora*"})
        self.assertEqual(self.calls[2][1]["ai_id"], "mochi1")
        # The other half of the time the side chatter stays quiet, and at bedtime they are asleep.
        self.calls.clear()
        with mock.patch.object(us.random, "random", lambda: 0.9):
            us.tell_lora("kindroid", "lora1", "*heads up, lora*")
        self.assertEqual(self.names(), ["groupchats-user-message", "groupchats-ai-response"])
        self.calls.clear()
        with mock.patch.object(us, "la_now", lambda: at(22)), mock.patch.object(us.random, "random", lambda: 0.1):
            us.tell_lora("kindroid", "lora1", "*goodnight*")
        self.assertEqual(self.names(), ["groupchats-user-message", "groupchats-ai-response"])

    def test_the_group_uses_the_names_from_setup(self) -> None:
        config = json.loads((self.tmp / "config.json").read_text(encoding="utf-8"))
        config["cast"] = {"kin_name": "Aria", "user_name": "Jay", "chatter_name": "Biscuit", "chatter_what": "orange cat"}
        (self.tmp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        cast.reset_cache()
        us.save_prefs(dict(us.load_prefs(), group_auto="1"))
        self.chat = [
            {"timestamp": 2, "sender": "ai", "display_name": "Biscuit", "message": "mrrp"},
            {"timestamp": 3, "sender": "ai", "display_name": "Aria", "message": "Narrator extend time please."},
        ]
        self.assertEqual(us.tell_lora("kindroid", "lora1", "*heads up, lora. Mochi and Master wave*"), "Narrator extend time please.")
        self.assertEqual(self.calls[0][1]["message"], "*heads up, aria. Biscuit and Jay wave*")
        us.save_kindroid("kindroid", "lora1", "lora at the pond, Mochi splashing")
        self.assertEqual(self.calls[-1][1]["current_scene"], "aria at the pond, Biscuit splashing")

    def test_the_scene_and_the_side_chatter_go_to_the_group(self) -> None:
        us.save_kindroid("kindroid", "lora1", "lora at the pond, Mochi splashing")
        self.assertEqual(self.calls[-1], ("groupchats-update", {"group_id": "grp1", "current_scene": "lora at the pond, Mochi splashing"}))
        self.assertEqual(us.mochi_talks(), "hi from mochi1")
        self.assertEqual(self.calls[-1][1], {"group_id": "grp1", "ai_id": "mochi1", "stream": False})
        self.assertEqual(us.reply_text("plain words"), "plain words")

    def test_switched_off_it_is_the_1_on_1_chat_like_before(self) -> None:
        us.save_prefs({"use_group": "0"})
        self.assertEqual(us.active_group(), "")
        self.assertEqual(us.tell_lora("kindroid", "lora1", "*heads up*"), "hi from the 1-on-1")
        us.save_kindroid("kindroid", "lora1", "lora at the pond")
        self.assertEqual(self.names(), ["send-message", "update-info"])
        with self.assertRaises(RuntimeError):
            us.mochi_talks()


class WipeTest(unittest.TestCase):
    """Wipe: everything saved about you goes, the app's own files stay, and the DeepSeek key can stay."""

    def test_wipe_leaves_a_clean_copy(self) -> None:
        import wipe

        root = Path(tempfile.mkdtemp())
        try:
            (root / ".env").write_text("DEEPSEEK_API_KEY=dk\nKINDROID_API_KEY=kk\nKINDROID_AI_ID=ai1\n", encoding="utf-8")
            for name in ("config.json", "state.json", "state.backup.json", "simulation.log", "simulation.log.1", "background.png", "gui.py", "README.txt"):
                (root / name).write_text("x", encoding="utf-8")
            for folder in ("kindroid-browser/Default", "backgrounds", "__pycache__"):
                (root / folder).mkdir(parents=True)
                (root / folder / "file").write_text("x", encoding="utf-8")
            self.assertEqual(wipe.wipe(keep_deepseek=True, root=root), [])
            self.assertEqual(sorted(path.name for path in root.iterdir()), [".env", "README.txt", "gui.py"])
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "DEEPSEEK_API_KEY=dk\n")
            # Before sharing: the key goes too.
            self.assertEqual(wipe.wipe(keep_deepseek=False, root=root), [])
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["README.txt", "gui.py"])
        finally:
            shutil.rmtree(root, ignore_errors=True)
            us.load_env(us.ENV_PATH)


class KindroidCallsTest(unittest.TestCase):
    def test_kin_name_comes_from_the_kins_own_chat(self) -> None:
        chat = [
            {"timestamp": 1, "sender": "user", "display_name": "Jay", "message": "hi"},
            {"timestamp": 2, "sender": "ai", "display_name": "Aria", "message": "hello"},
        ]
        with mock.patch.object(us, "fetch_messages", return_value=(chat, 2)) as fetch:
            self.assertEqual(us.kin_name("aria1", api_key="kk"), "Aria")
        fetch.assert_called_once_with(0, kin_id="aria1", api_key="kk")
        with mock.patch.object(us, "fetch_messages", return_value=([], 0)):
            self.assertEqual(us.kin_name("new-kin"), "")

    def test_a_slow_answer_is_a_normal_error(self) -> None:
        # A timeout while waiting for the answer is not a URLError. It must still count as "could not reach".
        with mock.patch.object(us.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(RuntimeError) as caught:
                us.request_body(us.DEEPSEEK_URL, {}, {}, timeout=1)
        self.assertIn("timed out", str(caught.exception))


class Base(unittest.TestCase):
    """A temp copy of the state, with DeepSeek and Kindroid replaced."""

    @classmethod
    def setUpClass(cls) -> None:
        source = ROOT / "state.json"
        real = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        prefs = real.get("prefs") or {}
        sample = ROOT / "test_environment.txt"
        if len(str(prefs.get("environment") or "")) < 1000 and sample.exists():
            prefs = {"environment": sample.read_text(encoding="utf-8")}
        if len(str(prefs.get("environment") or "")) < 1000:
            raise unittest.SkipTest("The full Environment list is not saved in state.json yet.")
        cls.prefs = dict(prefs)
        cls.lines = Lines()
        simulation.log.handlers[:] = [cls.lines]
        simulation.log.setLevel(logging.INFO)
        simulation.log.propagate = False

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.now = at(14)
        self.fake = Fakes()
        prefs = dict(self.prefs, last_greet_date=self.now.date().isoformat(), heads_up="0")
        state = {"history": [START], "prefs": prefs, "updated_at": at(13, 50).replace(tzinfo=None).isoformat()}
        (self.tmp / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        # The tests use a fixed map (only the bedroom door leads outside), not whatever the app's map is today.
        (self.tmp / "world_map.json").write_text(json.dumps(TEST_MAP), encoding="utf-8")
        # A made-up config with profile ids, like after setup, but with the example names.
        (self.tmp / "config.json").write_text(json.dumps(TEST_CONFIG), encoding="utf-8")
        cast.reset_cache()
        self.patches = [
            mock.patch.object(us, "CONFIG_PATH", self.tmp / "config.json"),
            mock.patch.object(cast, "CONFIG_PATH", self.tmp / "config.json"),
            mock.patch.object(us, "STATE_PATH", self.tmp / "state.json"),
            mock.patch.object(us, "BACKUP_PATH", self.tmp / "state.backup.json"),
            mock.patch.object(us, "load_env", lambda path: None),
            mock.patch.object(us, "require_keys", lambda: ("deepseek", "kindroid", "ai")),
            mock.patch.object(us, "ask_deepseek_json", self.fake.ask_json),
            mock.patch.object(us, "ask_deepseek", self.fake.ask),
            mock.patch.object(us, "switch_profile", self.fake.switch),
            mock.patch.object(us, "save_kindroid", self.fake.save),
            mock.patch.object(us, "tell_lora", self.fake.tell),
            mock.patch.object(us, "fetch_messages", self.fake.fetch),
            mock.patch.object(us, "la_now", lambda: self.now),
            mock.patch.object(simulation, "WORLD_MAP_PATH", self.tmp / "world_map.json"),
        ]
        for patch in self.patches:
            patch.start()
        self.lines.lines.clear()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        cast.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self) -> dict:
        return json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))

    def digest(self) -> str:
        return hashlib.sha256((self.tmp / "state.json").read_bytes()).hexdigest()

    def kitchen_step(self) -> dict:
        self.fake.plans = [
            plan(
                "kitchen-area",
                "lora slices fruit at the kitchen table, Mochi begging by the toaster, afternoon sun on the plates",
                objects=[{"id": "fruit bowl", "state": "half empty"}, {"id": "spaceship engine", "state": "on"}],
                memory={"text": "Lora shared apple slices with Mochi.", "importance": 2},
                new_goal={"title": "Bake cookies", "steps": ["gather ingredients", "mix dough", "bake", "share with Mochi"]},
                lights="ceiling lights",
            )
        ]
        return simulation.advance_simulation()



class SimulationTest(Base):
    def test_step_from_existing_setting(self) -> None:
        result = self.kitchen_step()
        world = self.state()["world"]
        self.assertEqual(world["journal"][-1]["from"], "fireplace")
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["mochi"]["location"], "kitchen-area")
        self.assertIn("kitchen-area", simulation.catalog_for(self.prefs["environment"])["places"])
        self.assertLessEqual(len(result["setting"]), us.SCENE_LIMIT)
        self.assertIn("Mochi", result["setting"])
        self.assertEqual(result["message"], us.narrator_message(START, result["setting"], bridge=result["bridge"]))
        self.assertEqual(self.fake.sent, [("setting", result["setting"]), ("message", result["message"])])
        self.assertEqual(self.fake.profiles[0], "Narrator")
        self.assertIn(self.fake.profiles[-1], {"Master", "Mochi"})
        self.assertEqual(self.state()["history"][-1], result["setting"])
        self.assertIn("kitchen-area:fruit-bowl", world["objects"])
        self.assertFalse(any("spaceship" in key for key in world["objects"]))
        self.assertTrue(any(item["text"] == "Lora shared apple slices with Mochi." for item in world["memories"]))
        self.assertEqual(world["goal"]["title"], "Bake cookies")
        self.assertFalse(result["pending"])

    def test_invented_place_and_teleport_are_rejected(self) -> None:
        self.kitchen_step()
        self.now = at(14, 10)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("spaceship", "lora floats in the spaceship with Mochi"),
            plan("greenhouse", "lora waters seed trays in the greenhouse, Mochi sniffing the pots"),
            plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel"),
        ]
        result = simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("is not a place in her world", self.fake.prompts[1])
        self.assertIn("no known way", self.fake.prompts[2])
        self.assertIn("living room", result["setting"])

    def test_move_check_saying_no_every_time_does_not_get_stuck(self) -> None:
        """At 6 PM the move check said no to every try, and nothing changed for 17 minutes."""
        self.now = at(17, 51)
        self.kitchen_step()
        self.now = at(18, 16)
        self.fake.fits = False
        self.fake.plans = [
            plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel"),
            plan("living-room", "lora stacks cushions on the living room sofa, Mochi chewing a cushion tassel"),
            plan("living-room", "lora dims the living room lamp by the sofa, Mochi chewing a cushion tassel"),
            plan("living-room", "lora sorts a basket on the living room sofa, Mochi chewing a cushion tassel"),
        ]
        result = simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("folds blankets", result["setting"])
        self.assertTrue(any("move check said no on every try" in line for line in self.lines.lines))

    def test_fallback_writer_keeps_a_line_the_move_check_refused(self) -> None:
        self.now = at(17, 51)
        self.kitchen_step()
        self.now = at(18, 16)
        self.fake.fits = False
        self.fake.plans = []
        self.fake.legacy = "lora carries the plates to the sink in the kitchen area, Mochi trotting behind, evening light"
        result = simulation.advance_simulation()
        self.assertEqual(result["setting"], self.fake.legacy)
        self.assertEqual(result["bridge"], "")

    def test_fallback_line_gets_a_bridge_and_real_length(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [ValueError("bad json")] * simulation.PLAN_TRIES + [
            {"bridge": "The blankets were still in a heap, so lora carried them over.", "minutes_reason": "folding a few blankets", "minutes": 8}
        ]
        self.fake.legacy = "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel, afternoon sun"
        result = simulation.advance_simulation(schedule={"every": 30, "times": [], "ai": True})
        self.assertEqual(result["bridge"], "the blankets were still in a heap, so lora carried them over.")
        self.assertEqual(result["minutes"], 8)
        self.assertEqual(result["delay"], 8 * 60)
        self.assertIn("then, the blankets were still in a heap", self.fake.sent[-1][1])

    def test_late_night_stays_in_bedroom_and_winds_down(self) -> None:
        self.kitchen_step()
        self.now = at(23, 30)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("kitchen-area", "lora rinses cups at the kitchen sink, Mochi yawning by the fridge"),
            plan("floor-rug", "lora curls up on the floor rug with Mochi, curtains drawn, the room quiet and dark"),
        ]
        simulation.advance_simulation()
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "floor-rug")
        self.assertIn("late at night", self.fake.prompts[1])
        self.assertEqual(world["goal"]["title"], "Wind down for the night")
        self.assertEqual(world["goals_paused"][-1]["title"], "Bake cookies")

    def test_hours_later_she_wakes_up(self) -> None:
        self.kitchen_step()
        self.now = at(23, 59)
        self.fake.plans = [plan("floor-rug", "lora curls up on the floor rug with Mochi, curtains drawn, the room quiet and dark")]
        simulation.advance_simulation()
        self.now = at(8, 0, day=25)
        self.fake.plans = [plan("window", "lora opens the window curtains for Mochi, morning sun across the room")]
        simulation.advance_simulation()
        world = self.state()["world"]
        self.assertIn("About 8 hours passed", self.fake.prompts[-1])
        self.assertIn("Morning", self.fake.prompts[-1])
        self.assertEqual(world["goals_done"][-1]["title"], "Wind down for the night")
        self.assertEqual(world["goal"]["title"], "Start the day")
        self.assertTrue(any("slept through the night" in item["text"] for item in world["memories"]))

    def test_failed_deepseek_keeps_state(self) -> None:
        self.kitchen_step()
        before = self.digest()
        self.now = at(14, 20)
        self.fake.plans = [RuntimeError("DeepSeek returned 503")] * simulation.PLAN_TRIES
        with self.assertRaises(RuntimeError):
            simulation.advance_simulation()
        self.assertEqual(self.digest(), before)
        self.assertEqual(len(self.fake.sent), 2)

    def test_broken_json_is_rejected_then_fallback_writer_used(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.prompts.clear()
        self.fake.plans = [ValueError("DeepSeek sent broken JSON.")] * simulation.PLAN_TRIES
        self.fake.legacy = "lora reads on the living room sofa, Mochi chewing a cushion, afternoon light"
        result = simulation.advance_simulation()
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("not a JSON object", self.fake.prompts[1])

    def test_failed_kindroid_keeps_local_state_and_retries(self) -> None:
        self.fake.fail_tell = True
        result = self.kitchen_step()
        world = self.state()["world"]
        self.assertTrue(result["pending"])
        self.assertEqual(world["setting"], result["setting"])
        self.assertEqual(world["pending"]["parts"], {"setting": True, "message": False})
        self.assertEqual(self.state()["history"][-1], result["setting"])
        self.fake.fail_tell = False
        self.fake.sent.clear()
        retry = simulation.advance_simulation()
        self.assertTrue(retry["delivered"])
        self.assertEqual(self.fake.sent, [("message", result["message"])])
        self.assertIsNone(self.state()["world"]["pending"])

    def test_schedule_and_restart(self) -> None:
        self.kitchen_step()
        next_at = self.now + timedelta(minutes=10)
        simulation.set_schedule(True, next_at)
        info = simulation.summary()
        self.assertTrue(info["running"])
        self.assertEqual(info["next_at"], next_at)
        self.assertEqual(info["place"], "Kitchen area (indoor)")
        self.assertEqual(info["goal"], "Bake cookies")
        simulation.set_schedule(False)
        self.assertFalse(simulation.schedule()["running"])
        self.assertEqual(simulation.parse_every("2h"), 120)
        self.assertEqual(simulation.parse_every("1h30m"), 90)
        self.assertEqual(simulation.parse_every("off"), 0)
        self.assertEqual(simulation.parse_times("8:00 AM, noon, night"), [480, 720, 1320])
        self.assertEqual(simulation.seconds_until_next(at(11, 59), [720]), 60)

    def test_backup_and_recovery(self) -> None:
        self.kitchen_step()
        before = (self.tmp / "state.json").read_text(encoding="utf-8")
        us.remember("lora waves at Mochi from the porch")
        self.assertEqual((self.tmp / "state.backup.json").read_text(encoding="utf-8"), before)
        (self.tmp / "state.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(us.read_state()["history"], json.loads(before)["history"])
        good = (self.tmp / "state.backup.json").read_bytes()

        def reject(state: dict) -> None:
            raise RuntimeError("invalid")

        with self.assertRaises(RuntimeError):
            us.update_state(lambda state: state.update(x=1), validate=reject)
        self.assertEqual((self.tmp / "state.backup.json").read_bytes(), good)

    def test_cli_dry_run_and_prior(self) -> None:
        self.fake.plans = [plan("kitchen-area", "lora rinses berries at the kitchen sink, Mochi watching the oven light")]
        before = self.digest()
        lines = us.run_update(dry_run=True)
        self.assertEqual(self.digest(), before)
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(any(line.startswith("Preview only") for line in lines))
        prior = "lora reads in the reading nook armchair with Mochi on the blanket"
        self.fake.plans = [plan("library", "lora climbs the rolling ladder in the library, Mochi waiting at the reading table")]
        lines = us.run_update(prior_override=prior)
        self.assertEqual(lines[0], f"Prior: {prior}")
        self.assertEqual(self.state()["world"]["journal"][-1]["from"], "reading-nook")
        self.assertEqual(self.state()["world"]["lora"]["location"], "library")

    def test_possessive_mochi_counts(self) -> None:
        self.assertTrue(us.has_mochi("lora ties Mochi's bow by the shoerack"))
        self.fake.plans = [plan("kitchen-area", "lora fills Mochi's water bowl at the kitchen sink, afternoon sun on the plates")]
        result = simulation.advance_simulation()
        self.assertEqual(result["source"], "planner")
        self.assertEqual(len(self.fake.prompts), 1)

    def test_listed_time_close_by_is_not_skipped(self) -> None:
        self.assertEqual(simulation.seconds_until_next(at(11, 59).replace(second=45), [720]), 15)
        just_fired = at(12, 0).replace(microsecond=500_000)
        self.assertGreater(simulation.seconds_until_next(just_fired, [720]), 23 * 3600)

    def test_partial_profile_switch_is_switched_back(self) -> None:
        personas: list[str] = []

        def post(url, payload, headers, timeout, required=False):
            if "active_persona_id" in payload:
                personas.append(payload["active_persona_id"])
            elif payload.get("user_name") == "Narrator":
                raise RuntimeError("update-info returned 429: slow down")
            return {}

        config = us.load_json(us.CONFIG_PATH, {})
        with mock.patch.object(us, "switch_profile", REAL_SWITCH), mock.patch.object(us, "post_json", post):
            with self.assertRaises(us.PublishError):
                us.publish_change("before line", "after line")
        self.assertEqual(personas[0], config["narrator_profile"]["id"])
        self.assertEqual(personas[-1], config["master_profile"]["id"])

    def test_newest_master_message_beats_an_older_mochi_page(self) -> None:
        calls = {"n": 0}

        def fetch(after, group_id=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return (
                    [
                        {"timestamp": after + 1 + i, "sender": "user", "display_name": "Mochi", "message": "woof"}
                        for i in range(20)
                    ],
                    0,
                )
            return ([{"timestamp": after + 5, "sender": "user", "display_name": "Master", "message": "hi"}], 0)

        with mock.patch.object(us, "fetch_messages", fetch):
            profile = us.chatter_profile(us.load_json(us.CONFIG_PATH, {}))
        self.assertEqual(profile["user_name"], "Master")
        self.assertEqual(calls["n"], 2)

    def test_request_goes_through_and_is_remembered(self) -> None:
        self.now = at(23, 0)
        self.fake.plans = [plan("kitchen-area", "lora warms milk at the kitchen stove for Master, Mochi at her heels")]
        simulation.advance_simulation(request="take her to the kitchen for warm milk", requested_by="Master")
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertTrue(any(item["kind"] == "request" and "Master" in item["who"] for item in world["memories"]))

    def test_lora_narrator_request_picks_that_place(self) -> None:
        self.fake.plans = [
            plan(
                "garden-vegetable-patch",
                "lora waters the garden vegetable patch, Mochi sniffing tomatoes, morning sun on the carrots",
            )
        ]
        simulation.advance_simulation()
        self.now = at(14, 10)
        stamp = int(self.now.timestamp() * 1000)
        old = [
            {"timestamp": stamp - 30_000 - i, "sender": "ai", "display_name": "Lora", "message": f"chat {i}"}
            for i in range(20)
        ]
        ask = {
            "timestamp": stamp,
            "sender": "ai",
            "display_name": "Lora",
            "message": "Hey @narrator, I want to go to the treehouse with the telescope.",
        }
        pages = iter([(old, 0), ([ask], 0)])
        self.fake.fetch = lambda after, group_id=None: next(pages, ([ask], 0))
        us.fetch_messages = self.fake.fetch
        self.fake.plans = [
            plan(
                "walking-path",
                "lora stretches her arms on the walking path, Mochi sniffing around, morning sun on the flower beds",
            ),
            plan(
                "treehouse",
                "lora climbs into the treehouse with Mochi, telescope by the tiny window, morning sun on the cushions",
            ),
        ]
        result = simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "treehouse")
        self.assertIn("treehouse", result["setting"])

    def test_deepseek_picks_how_long_the_action_lasts(self) -> None:
        self.assertEqual(simulation.activity_minutes(300), 120)
        self.assertEqual(simulation.activity_minutes(2), 5)
        self.assertEqual(simulation.activity_minutes("25"), 25)
        self.assertIsNone(simulation.activity_minutes("soon"))
        self.assertIsNone(simulation.activity_minutes(True))
        self.fake.plans = [
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", minutes=25)
        ]
        result = simulation.advance_simulation()
        self.assertEqual(result["minutes"], 25)
        self.assertEqual(self.state()["world"]["activity_minutes"], 25)
        self.assertIn('"minutes"', self.fake.prompts[0])

    def test_narrator_announces_time_until_next_change(self) -> None:
        self.assertEqual(us.duration_text(60), "1 minute")
        self.assertEqual(us.duration_text(1200), "20 minutes")
        self.assertEqual(us.duration_text(5400), "1 hour 30 minutes")
        self.assertEqual(us.duration_text(7200), "2 hours")
        self.fake.plans = [
            plan(
                "kitchen-area",
                "lora slices fruit at the kitchen table, Mochi begging by the toaster",
                minutes=12,
                minutes_reason="slicing one bowl of fruit, about 12 minutes",
                bridge="Her stomach growled as the log burned down, so lora got up from the rug and padded to the kitchen, Mochi right behind her.",
            )
        ]
        result = simulation.advance_simulation(schedule={"every": 10, "times": [], "ai": True})
        message = self.fake.sent[-1][1]
        lines = message.splitlines()
        self.assertEqual(lines[0], f"*before, {START}")
        self.assertEqual(
            lines[1],
            "then, her stomach growled as the log burned down, so lora got up from the rug and padded to the kitchen, Mochi right behind her.",
        )
        self.assertEqual(lines[2], f"now, {result['setting']}*")
        self.assertEqual(lines[3], "time until next change: 12 minutes")
        self.assertEqual(len(lines), 4)
        self.assertNotIn("conversate", message)
        self.assertEqual(result["delay"], 12 * 60)
        self.assertTrue(any(line == "length: 12 minutes, slicing one bowl of fruit, about 12 minutes" for line in self.lines.lines))
        world = self.state()["world"]
        self.assertEqual(world["journal"][-1]["bridge"], result["bridge"])

    def test_bridge_is_required_and_carried_into_the_next_step(self) -> None:
        self.fake.plans = [
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", bridge=""),
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", bridge="Hungry, lora walked to the kitchen with Mochi."),
        ]
        result = simulation.advance_simulation()
        self.assertIn('"bridge" was empty', self.fake.prompts[1])
        self.assertEqual(result["bridge"], "hungry, lora walked to the kitchen with Mochi.")
        prompt = self.fake.prompts[0]
        self.assertNotIn('"minutes": 20', prompt)
        self.assertIn("hang one load of laundry 10 to 15", prompt)
        self.assertIn('"minutes_reason"', prompt)
        self.now = at(14, 20)
        self.fake.prompts.clear()
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel")]
        simulation.advance_simulation()
        self.assertIn("How she got here: hungry, lora walked to the kitchen with Mochi.", self.fake.prompts[0])

    def test_clean_bridge(self) -> None:
        self.assertEqual(us.clean_bridge("Then, *The sun set* and lora went in."), "the sun set and lora went in.")
        self.assertEqual(us.clean_bridge("Mochi tugged her sleeve toward the porch."), "Mochi tugged her sleeve toward the porch.")
        self.assertEqual(us.clean_bridge(None), "")
        self.assertEqual(us.narrator_message("a", "b"), "*before, a\nnow, b*")
        self.assertEqual(us.extension_message("5 minutes"), "*lora stays where she is a little longer*\ntime until next change: 5 minutes")

    def test_extend_command_is_found_but_narrator_lines_are_skipped(self) -> None:
        messages = [
            {"timestamp": 1, "sender": "user", "display_name": "Narrator", "message": us.narrator_message("a", "b", "5 minutes")},
            {"timestamp": 2, "sender": "ai", "display_name": "Lora", "message": "[yawns] Narrator extend time please"},
        ]
        self.assertEqual(us.narrator_command_from(messages), ("extend", "Lora", "", 2))
        messages.append({"timestamp": 3, "sender": "ai", "display_name": "Lora", "message": "hey @narrator the library"})
        self.assertEqual(us.narrator_command_from(messages)[:3], ("go", "Lora", "the library"))

    def test_extend_keeps_her_there_once(self) -> None:
        schedule = {"every": 10, "times": [], "ai": True}
        self.fake.plans = [
            plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi begging by the toaster", minutes=20)
        ]
        first = simulation.advance_simulation(schedule=schedule)
        self.now = at(14, 20)
        self.fake.sent.clear()
        self.fake.plans = []
        stamp = int(self.now.timestamp() * 1000)
        result = simulation.advance_simulation(schedule=schedule, extend_at=stamp, requested_by="Lora")
        world = self.state()["world"]
        self.assertTrue(result["extended"])
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["setting"], first["setting"])
        self.assertEqual(world["last_extend_at"], stamp)
        self.assertEqual(self.fake.sent[0][0], "message")
        self.assertIn("time until next change: 20 minutes", self.fake.sent[0][1])
        self.assertEqual(result["delay"], 20 * 60)
        self.now = at(14, 40)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel")]
        again = simulation.advance_simulation(schedule=schedule, extend_at=stamp, requested_by="Lora")
        self.assertFalse(again["extended"])
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")

    def test_request_naming_two_places_uses_the_later_one(self) -> None:
        catalog = simulation.catalog_for(self.prefs["environment"])
        self.assertEqual(simulation.requested_place(catalog, "leave the kitchen and go to the library"), "library")
        self.assertEqual(simulation.requested_place(catalog, "I want to go to the treehouse with the telescope"), "treehouse")

    def test_carried_out_request_is_not_applied_again(self) -> None:
        self.fake.plans = [plan("treehouse", "lora climbs into the treehouse with Mochi, telescope by the tiny window")]
        simulation.advance_simulation(request="take me to the treehouse", requested_by="Lora")
        self.assertEqual(self.state()["world"]["lora"]["location"], "treehouse")
        self.now = at(14, 10)
        self.fake.prompts.clear()
        self.fake.plans = [plan("playground", "lora swings on the playground swing set, Mochi chasing the seesaw shadow")]
        simulation.advance_simulation(request="take me to the treehouse", requested_by="Lora")
        self.assertNotIn("Request from the chat", self.fake.prompts[0])
        self.assertEqual(self.state()["world"]["lora"]["location"], "playground")


CONSERVATORY = {
    "name": "Hidden glass conservatory",
    "type": "indoor",
    "description": "A small glass room hidden behind the backyard fence, warm and full of ferns.",
    "objects": ["ferns", "wicker chair", "glass roof"],
    "connection_from": "backyard",
    "via": "a gap in the backyard fence",
}
INTO_CONSERVATORY = "lora slips into the hidden glass conservatory with Mochi, ferns brushing the wicker chair"


def discover(**changes) -> dict:
    return plan("new", INTO_CONSERVATORY, new_location=dict(CONSERVATORY, **changes))


class ChangeCommandTest(Base):
    def test_weather_command_changes_weather_and_she_stays(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi by the toaster, rain on the window", weather="clear")]
        result = simulation.advance_simulation(request="rain", requested_by="lora", check_chat=False, weather="rain")
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["weather"], "rain")
        prompt = self.fake.prompts[-1]
        self.assertIn('"location" must be "kitchen-area"', prompt)
        self.assertIn("The weather is now rain.", prompt)
        self.assertIn("lora stays where she is", prompt)
        self.assertIn("rain on the window", result["setting"])

    def test_change_command_keeps_her_in_place(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora slices fruit at the kitchen table under dimmed lights, Mochi by the toaster")]
        simulation.advance_simulation(request="turn the lights down", requested_by="lora", check_chat=False, stay_here=True)
        self.assertEqual(self.state()["world"]["lora"]["location"], "kitchen-area")
        self.assertIn("turn the lights down. lora stays where she is", self.fake.prompts[-1])

    def test_same_change_asked_again_is_still_done(self) -> None:
        self.kitchen_step()
        for minute in (20, 40):
            self.now = at(14, minute)
            self.fake.plans = [plan("kitchen-area", f"lora at the kitchen table under dimmed lights at 2:{minute}, Mochi by the toaster")]
            result = simulation.advance_simulation(request="turn the lights down", requested_by="lora", check_chat=False, stay_here=True)
            self.assertIn("dimmed lights", result["setting"])


class ExactRequestTest(Base):
    def test_a_request_is_narrated_exactly(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        words = "I'm going to the living room to fold the blankets, I can't sit still"
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(request=words, requested_by="lora", check_chat=False)
        prompt = self.fake.prompts[-1]
        self.assertIn(f'lora asked for this, in these words: "{words}"', prompt)
        self.assertIn("Narrate exactly what was asked.", prompt)
        self.assertIn("Do not add new actions for her", prompt)

    def test_an_automatic_move_has_no_request_rules(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(check_chat=False)
        self.assertNotIn("Narrate exactly what was asked", self.fake.prompts[-1])

    def test_weather_request_words_are_the_weather(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora slices fruit at the kitchen table, Mochi by the toaster, rain on the window")]
        simulation.advance_simulation(request="rain", requested_by="lora", check_chat=False, weather="rain")
        self.assertIn('lora asked for this, in these words: "change the weather to rain"', self.fake.prompts[-1])


class RequestIsNewTest(Base):
    def test_request_is_new(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        wish = "take me to the living room"
        self.assertTrue(simulation.request_is_new(wish))
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(request=wish, requested_by="lora", check_chat=False)
        self.assertFalse(simulation.request_is_new(wish))


class ExtendStreakTest(Base):
    def test_extends_are_counted_and_reset_when_she_moves(self) -> None:
        self.kitchen_step()
        arrived = self.state()["world"]["here_since"]
        base = int(time.time() * 1000)
        for count in (1, 2, 3):
            self.now = at(14, 10 * count)
            result = simulation.advance_simulation(extend_at=base + count, check_chat=False)
            self.assertTrue(result["extended"])
            self.assertEqual(self.state()["world"]["extend_streak"], count)
        self.assertTrue(any("extend 3 in a row" in line for line in self.lines.lines))
        summary = simulation.here_summary(at(14, 40))
        self.assertTrue(summary["where"].startswith("at the kitchen area"), summary)
        self.assertEqual(summary["how_long"], "40 minutes")
        self.assertEqual(summary["extends"], 3)

        self.now = at(14, 45)
        self.fake.plans = [plan("kitchen-area", "lora at the kitchen table, Mochi by the toaster, rain on the window")]
        simulation.advance_simulation(request="rain", requested_by="lora", check_chat=False, weather="rain")
        world = self.state()["world"]
        self.assertEqual(world["extend_streak"], 0)
        self.assertEqual(world["here_since"], arrived)

        self.now = at(15, 0)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(request="take me to the living room", requested_by="lora", check_chat=False)
        self.assertEqual(self.state()["world"]["here_since"], simulation.iso(at(15, 0)))


    def test_world_panel_shows_how_long_and_extends(self) -> None:
        import gui

        self.kitchen_step()
        slides = self.tmp / "slides"
        slides.mkdir()
        with mock.patch.object(gui, "SLIDES_DIR", slides):
            app = gui.App()
            app.withdraw()
            try:
                self.now = at(14, 5)
                app.refresh_world()
                self.assertEqual(app.world_rows["Here for"].cget("text"), "5 minutes")
                self.assertEqual(app.world_rows["Extended"].cget("text"), "not yet")
                base = int(time.time() * 1000)
                for count in (1, 2, 3):
                    self.now = at(14, 10 * count)
                    simulation.advance_simulation(extend_at=base + count, check_chat=False)
                    app.refresh_world()
                    if count == 1:
                        self.assertEqual(app.world_rows["Extended"].cget("text"), "1 time in a row")
                self.assertEqual(app.world_rows["Here for"].cget("text"), "30 minutes")
                self.assertEqual(
                    app.world_rows["Extended"].cget("text"), "3 times in a row · the heads-up suggests a change"
                )
            finally:
                app.destroy()


class GoCommandTest(Base):
    """16:30 on 9/25: "Narrator take me home" was missed and she was moved to the wishing well instead."""

    def command(self, text: str) -> tuple:
        return us.narrator_command_from([{"message": text, "sender": "ai", "display_name": "Lora", "timestamp": 5}])

    def test_narrator_take_me_home(self) -> None:
        text = (
            "Narrator take me home. [stands up slowly and lifts Mochi into my arms] ...The bridge is nice, but... "
            "I want to be in our room."
        )
        kind, who, body, _ = self.command(text)
        self.assertEqual((kind, who), ("go", "Lora"))
        self.assertEqual(body, "take me home. ...The bridge is nice, but... I want to be in our room")
        catalog = simulation.catalog_for(self.prefs["environment"])
        self.assertEqual(simulation.requested_place(catalog, body), "bedroom")

    def test_other_ways_to_ask_to_go(self) -> None:
        cases = {
            "hey narrator take me to the kitchen": "take me to the kitchen",
            "Hey, @narrator take me home": "take me home",
            "Narrator, I want to go to the greenhouse": "I want to go to the greenhouse",
            "narrator, can we go inside?": "can we go inside?",
            "Narrator. Let's go to bed": "Let's go to bed",
            "narrator bring me back to the porch": "bring me back to the porch",
            "[*smiles*] hey @narrator take me to the pond. [*picks up Mochi*] ...come on": "take me to the pond. ...come on",
        }
        for text, body in cases.items():
            self.assertEqual(self.command(text)[:3], ("go", "Lora", body), text)

    def test_not_a_move(self) -> None:
        self.assertEqual(self.command("narrator bring me a blanket please")[0], "change")
        self.assertEqual(self.command("narrator make it night")[0], "change")
        self.assertEqual(self.command("Narrator extend time please")[0], "extend")
        self.assertEqual(self.command("I love the narrator, he takes me places")[0], "")


class GoesWhenAskedTest(Base):
    """8:31 AM on 9/26: "Hey @narrator, take me home" and the planner kept her in the field."""

    def test_home_is_the_bedroom(self) -> None:
        catalog = simulation.catalog_for(self.prefs["environment"])
        for words in ("take me home. ...Master should see these", "can we go inside?", "let's go to bed"):
            self.assertEqual(simulation.requested_place(catalog, words), "bedroom", words)
        self.assertEqual(simulation.requested_place(catalog, "take me to the kitchen"), "kitchen-area")

    def test_take_me_home_is_not_turned_into_staying(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [
            plan("kitchen-area", "lora standing at the kitchen table, Mochi by the toaster, looking at the fruit"),
            plan("bedroom", "lora curls up on the bedroom rug by the window, Mochi in her lap"),
        ]
        simulation.advance_simulation(request="take me home. ...Master should see these", requested_by="Lora", check_chat=False)
        self.assertEqual(self.state()["world"]["lora"]["location"], "bedroom")
        self.assertIn('"location" must be "bedroom"', self.fake.prompts[1])

    def test_a_move_request_without_a_place_still_moves_her(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        wish = "I want to go somewhere else for a bit"
        catalog = simulation.catalog_for(self.prefs["environment"])
        self.assertIsNone(simulation.requested_place(catalog, wish))
        self.fake.plans = [
            plan("kitchen-area", "lora standing at the kitchen table, Mochi by the toaster, looking at the fruit"),
            plan("living-room", "lora folds blankets on the living room sofa, Mochi with her"),
        ]
        simulation.advance_simulation(request=wish, requested_by="Lora", check_chat=False)
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")
        self.assertIn("she asked to go somewhere, so she leaves the kitchen area", self.fake.prompts[-1])

    def test_a_request_that_is_not_a_move_can_stay(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", "lora reads a recipe book at the kitchen table, Mochi by the toaster")]
        simulation.advance_simulation(request="I want to read a recipe book", requested_by="Lora", check_chat=False)
        self.assertEqual(self.state()["world"]["lora"]["location"], "kitchen-area")


NOOK = {
    "name": "Herb window nook",
    "description": "A small sunny nook behind the pantry door with a cushioned sill and pots of herbs.",
    "objects": ["cushioned sill", "pots of basil", "watering can", "little stool"],
    "via": "through the pantry door",
    "notice": "you notice the pantry door is open a crack, and sunlight spills out from a little nook behind it.",
}


class DiscoveryOfferTest(Base):
    """Once an hour in the day, the heads-up offers her a new place. She decides."""

    def test_she_is_offered_a_new_place_next_to_her(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [dict(NOOK)]
        line = simulation.discovery_offer()
        self.assertEqual(
            line,
            "by the way, lora: you notice the pantry door is open a crack, and sunlight spills out from a little nook "
            'behind it. if you\'d like to go see it, just say "hey @narrator take me to the herb window nook".',
        )
        world = self.state()["world"]
        self.assertEqual(world["discovered"]["herb-window-nook"]["from"], "kitchen-area")
        self.assertEqual(world["discovered"]["herb-window-nook"]["zone"], "indoor")
        self.assertEqual(world["offer"]["kind"], "new")
        self.assertTrue(any("noticed the herb window nook" in item["text"] for item in world["memories"]))
        self.assertIn("It is indoor", self.fake.prompts[-1])
        # Not again within the hour. After an hour, a real place she has not been to when nothing new comes.
        self.now = at(14, 50)
        self.assertEqual(simulation.discovery_offer(), "")
        self.now = at(15, 25)
        line = simulation.discovery_offer()
        self.assertIn("you haven't been to the", line)
        self.assertIn('"hey @narrator take me to the', line)
        self.assertEqual(self.state()["world"]["offer"]["kind"], "known")

    def test_no_offers_in_the_evening_or_at_night(self) -> None:
        self.kitchen_step()
        for hour in (19, 22, 3):
            self.now = at(hour, 10)
            self.assertEqual(simulation.discovery_offer(), "")
        self.assertEqual(self.fake.prompts[1:], [])

    def test_a_copy_of_an_existing_place_is_not_offered(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [dict(NOOK, name="Kitchen area")]
        line = simulation.discovery_offer()
        self.assertIn("you haven't been to the", line)
        self.assertNotIn("herb-window-nook", self.state()["world"].get("discovered", {}))

    def test_she_can_go_there_now_or_later(self) -> None:
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.plans = [dict(NOOK)]
        simulation.discovery_offer()
        # Later, from the living room: she walks back by the kitchen to reach it.
        self.now = at(14, 40)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi with her")]
        simulation.advance_simulation(request="take me to the living room", requested_by="Lora", check_chat=False)
        self.now = at(15, 0)
        self.fake.plans = [plan(
            "herb-window-nook",
            "lora sits on the cushioned sill in the herb window nook, pots of basil around her, Mochi at her feet",
            bridge="lora remembered the sunny nook behind the pantry door and went to see it with Mochi.",
        )]
        simulation.advance_simulation(request="take me to the herb window nook", requested_by="Lora", check_chat=False)
        self.assertEqual(self.state()["world"]["lora"]["location"], "herb-window-nook")
        self.assertIn('"location" must be "herb-window-nook"', self.fake.prompts[-1])

    def test_the_offer_goes_in_the_heads_up(self) -> None:
        line = 'by the way, lora: you notice a path. if you\'d like to go see it, just say "hey @narrator take me to the path".'
        message = us.heads_up_message("1 minute", "at the kitchen area", "20 minutes", 0, offer=line)
        self.assertIn(line + "\n", message)
        self.assertLess(message.index(line), message.index("Narrator extend time please"))
        self.assertNotIn("by the way", us.heads_up_message("1 minute"))


class DreamTest(Base):
    """Asleep: she falls asleep where she is, and dreams come from her life."""

    def test_the_dream_message_fits_kindroid(self) -> None:
        dream = ("You float over the yard. " * 400).strip() + "\n\n*A second part.*"
        message = us.dream_message(dream)
        self.assertLessEqual(len(message), us.KINDROID_MESSAGE_LIMIT)
        self.assertTrue(message.startswith(us.DREAM_INTRO))
        self.assertIn("please don't talk to the narrator", message)
        body = message[len(us.DREAM_INTRO):].strip()
        self.assertTrue(body.startswith("*You float") and body.endswith(".*"))
        short = us.dream_message("One.\nTwo.")
        self.assertTrue(short.endswith("*One.*\n\n*Two.*"))

    def test_she_falls_asleep_where_she_is(self) -> None:
        self.kitchen_step()
        self.now = at(21, 30)
        before = len(self.fake.sent)
        result = simulation.fall_asleep()
        self.assertEqual(result["setting"], "lora fast asleep at the kitchen area, Mochi curled up beside her, dreaming")
        world = self.state()["world"]
        self.assertEqual(world["lora"]["activity"], "fast asleep, dreaming")
        self.assertEqual(world["asleep_since"], simulation.iso(at(21, 30)))
        messages = [text for kind, text in self.fake.sent[before:] if kind == "message"]
        self.assertTrue(messages[-1].startswith("*lora drifts off to sleep"))

    def test_a_dream_comes_from_her_life_and_is_remembered(self) -> None:
        self.kitchen_step()
        self.now = at(22, 0)
        dream = simulation.write_dream(rng=random.Random(2))
        self.assertIn("sunflower field", dream)
        prompt = self.fake.prompts[-1]
        self.assertIn("Write the dream Lora is having right now", prompt)
        self.assertRegex(prompt, r"Length: [^\n]+, about \d+ to \d+ words \(\d+ to \d+ characters\)\. Never longer\.")
        # Different dreams ask for different lengths, from a quick flash to a very long one. Short ones come more often.
        asked = []
        for seed in range(200):
            simulation.write_dream(rng=random.Random(seed))
            asked.append(re.search(r"\((\d+) to (\d+) characters", self.fake.prompts[-1]).group(1))
        self.assertEqual(set(asked), {"250", "600", "1200", "2200", "3000"})
        self.assertGreater(asked.count("600"), asked.count("3000") * 2)

        self.assertIn("nothing sexual", prompt)
        self.assertIn("Simple, everyday words in normal sentences that flow", prompt)
        self.assertIn("Smooth, seamless transitions", prompt)
        self.assertIn("use simple words, don't be poetic. use 1st person", us.DREAM_INTRO)
        self.assertIn("No comparisons or metaphors", prompt)
        simulation.remember_dream(dream)
        world = self.state()["world"]
        self.assertTrue(world["memories"][-1]["text"].startswith("Lora dreamed: You are standing in the sunflower field"))
        self.fake.dream = "too short"
        with self.assertRaises(RuntimeError):
            simulation.write_dream()

    def test_a_dream_that_runs_long_is_cut_at_a_paragraph_or_sentence(self) -> None:
        long_dream = ("You drift. " * 40).strip() + "\n" + ("The pond hums. " * 40).strip()
        fitted = simulation.fit_dream(long_dream, 600)
        self.assertEqual(fitted, ("You drift. " * 40).strip())
        one_paragraph = "The moon hums softly over the thatch and the reeds. " * 30
        fitted = simulation.fit_dream(one_paragraph, 600)
        self.assertLessEqual(len(fitted), 600)
        self.assertTrue(fitted.endswith("reeds."))
        self.assertEqual(simulation.fit_dream("Short and sweet.", 600), "Short and sweet.")


class ExtendNudgeTest(Base):
    """The more she extends, the warmer the nudge. After EXTEND_LIMIT she is moved on."""

    def set_streak(self, count: int) -> None:
        simulation._update_world(lambda saved: saved.update(extend_streak=count))

    def test_nudges_grow_and_never_name_a_place(self) -> None:
        texts = {n: us.heads_up_message("1 minute", "at the small pond", "1 hour 15 minutes", n) for n in range(0, 10)}
        for n in (0, 1, 2):
            self.assertNotIn("change of scenery", texts[n])
            self.assertNotIn("Mochi", texts[n])
        self.assertIn("change of scenery", texts[3])
        self.assertIn("of course, you're welcome to stay", texts[4])
        self.assertIn("of course, you're welcome to stay", texts[5])
        self.assertIn("i'd really suggest stretching your legs", texts[6])
        self.assertNotIn("next time i'll take you somewhere new", texts[6])
        self.assertIn("next time i'll take you somewhere new", texts[7])
        self.assertIn("this time, when the minute is up, i'll take you somewhere new", texts[8])
        # Moving comes first from the 4th extend, and extending is not offered at the limit.
        for n in (0, 3):
            self.assertLess(texts[n].index("Narrator extend time please"), texts[n].index("if you would like to do something else"))
        for n in (4, 6, 7):
            self.assertLess(texts[n].index("if you would like to do something else"), texts[n].index("Narrator extend time please"))
        for n in (8, 9):
            self.assertNotIn("Narrator extend time please", texts[n])
            self.assertIn("please dont conversate with me.", texts[n])
        catalog = simulation.catalog_for(self.prefs["environment"])
        names = {place["name"].lower() for pid, place in catalog["places"].items() if len(place["name"]) > 5}
        for n, text in texts.items():
            nudge = us.nudge_text(n, "1 hour")
            self.assertFalse([name for name in names if name in nudge.lower()], (n, nudge))

    def test_extend_moves_the_moment_forward(self) -> None:
        self.kitchen_step()
        arrived = self.state()["world"]["here_since"]
        self.now = at(14, 20)
        self.fake.plans = [plan(
            "kitchen-area",
            "lora rinses the fruit knife at the kitchen sink, Mochi licking juice off the floor",
            bridge="lora asked to stay a little longer, so she finished the last slice and rinsed the knife.",
        )]
        before = len(self.fake.sent)
        result = simulation.advance_simulation(extend_at=int(time.time() * 1000), requested_by="Lora", check_chat=False)
        self.assertTrue(result["extended"])
        self.assertIn("lora asked to stay at the kitchen area a little longer", self.fake.prompts[-1])
        self.assertIn("The same place as now", self.fake.prompts[-1])
        world = self.state()["world"]
        self.assertEqual(world["extend_streak"], 1)
        self.assertEqual(world["here_since"], arrived)
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        messages = [text for kind, text in self.fake.sent[before:] if kind == "message"]
        self.assertTrue(messages[-1].startswith("*lora stays where she is a little longer.\nthen, lora asked to stay"), messages)
        self.assertIn("now, lora rinses the fruit knife", messages[-1])
        self.assertTrue(any("(extend 1 in a row)" in line for line in self.lines.lines))

    def test_extend_follows_where_she_went_in_the_chat(self) -> None:
        # She walked to another room in the chat, then asked to stay: the narrator follows her, it does not pull her back.
        self.kitchen_step()
        self.now = at(14, 20)
        self.fake.chat = [
            {"timestamp": 1, "sender": "ai", "display_name": "Lora", "message": "*carries the fruit bowl into the living room with Mochi*"},
            {"timestamp": 2, "sender": "user", "display_name": "Narrator", "message": "*heads up, lora*"},
            {"timestamp": 3, "sender": "ai", "display_name": "Lora", "message": '"Narrator extend time please." *curls up on the sofa*'},
        ]
        self.fake.plans = [plan(
            "living-room",
            "lora curls up on the living room sofa with the fruit bowl, Mochi on her feet",
            bridge="lora had already carried the fruit bowl to the living room, so she curled up on the sofa there.",
        )]
        result = simulation.advance_simulation(extend_at=int(time.time() * 1000), requested_by="Lora", check_chat=False)
        prompt = self.fake.prompts[-1]
        self.assertIn("curls up on the sofa", prompt)
        self.assertIn("carries the fruit bowl into the living room", prompt)
        self.assertNotIn("heads up, lora", prompt)
        self.assertIn("walk to from here (the living room)", prompt)
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "living-room")
        self.assertEqual(world["extend_streak"], 0)
        self.assertTrue(any("the narrator follows her there" in line for line in self.lines.lines))
        messages = [text for kind, text in self.fake.sent if kind == "message"]
        self.assertTrue(messages[-1].startswith("*before,"), messages[-1])

    def test_extend_without_a_new_moment_just_stays(self) -> None:
        self.kitchen_step()
        scene = self.state()["world"]["setting"]
        self.now = at(14, 20)
        self.fake.plans = [plan("kitchen-area", scene)] * 4
        result = simulation.advance_simulation(extend_at=int(time.time() * 1000), check_chat=False)
        self.assertTrue(result["extended"])
        self.assertEqual(result["setting"], scene)
        self.assertEqual(self.state()["world"]["extend_streak"], 1)
        self.assertTrue(any("she just stays as she is" in line for line in self.lines.lines))

    def test_walkable_pick_is_reachable_and_roomy(self) -> None:
        self.kitchen_step()
        state = self.state()
        world = simulation.load_world(state)
        catalog = simulation.catalog_for(self.prefs["environment"])
        links = simulation.load_map()
        ctx = {"catalog": catalog, "links": links, "world": world, "wish": "", "late_night": False}
        ctx["allowed"], _ = simulation._candidates(ctx, random.Random(1))
        for seed in range(20):
            pick = simulation.walkable_pick(ctx, random.Random(seed))
            self.assertNotEqual(pick, "kitchen-area")
            self.assertTrue(simulation.can_move(catalog, links, "kitchen-area", pick)[0])
            self.assertGreaterEqual(len(catalog["places"][pick]["items"]), 5, pick)

    def test_after_the_limit_an_extend_moves_her_somewhere_she_can_walk(self) -> None:
        self.kitchen_step()
        self.set_streak(us.EXTEND_LIMIT)
        self.now = at(14, 20)
        with mock.patch.object(simulation, "walkable_pick", lambda ctx, rng, **_: "living-room"):
            self.fake.plans = [plan(
                "living-room",
                "lora folds blankets on the living room sofa, Mochi with her",
                bridge="lora had stayed in the kitchen a long time, so the narrator asked her to try somewhere new.",
            )]
            result = simulation.advance_simulation(extend_at=int(time.time() * 1000), requested_by="Lora", check_chat=False)
        self.assertFalse(result["extended"])
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "living-room")
        self.assertEqual(world["extend_streak"], 0)
        self.assertIn("the narrator kindly asked lora to try somewhere new", self.fake.prompts[-1])
        self.assertTrue(any("so the Narrator moves her somewhere new" in line for line in self.lines.lines))
        self.assertTrue(any("the Narrator picked the living room" in line for line in self.lines.lines))

    def test_after_the_limit_with_no_plan_she_still_walks_there(self) -> None:
        self.kitchen_step()
        self.set_streak(us.EXTEND_LIMIT + 1)
        self.now = at(14, 20)
        with mock.patch.object(simulation, "walkable_pick", lambda ctx, rng, **_: "living-room"):
            simulation.advance_simulation(check_chat=False)
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "living-room")
        self.assertIn("the narrator asked her to try somewhere new", world["bridge"])

    def test_after_the_limit_her_own_choice_wins(self) -> None:
        self.kitchen_step()
        self.set_streak(us.EXTEND_LIMIT)
        self.now = at(14, 20)
        self.fake.plans = [plan("bathroom", "lora runs a warm bath in the bathroom, Mochi sitting on the mat")]
        with mock.patch.object(simulation, "walkable_pick", side_effect=AssertionError("should not pick")):
            simulation.advance_simulation(request="take me to the bathroom", requested_by="Lora", check_chat=False)
        self.assertEqual(self.state()["world"]["lora"]["location"], "bathroom")


class NightExtendTest(Base):
    """Evening (7 to 9 PM): a move after the limit stays inside. Bedtime (from 9 PM): no nudges, no moving on."""

    def set_streak(self, count: int, when) -> None:
        simulation._update_world(lambda saved: saved.update(extend_streak=count, extend_streak_at=simulation.iso(when)))

    def test_day_parts(self) -> None:
        self.assertEqual(us.day_part(at(18, 59)), "day")
        self.assertEqual(us.day_part(at(19, 0)), "evening")
        self.assertEqual(us.day_part(at(20, 59)), "evening")
        self.assertEqual(us.day_part(at(21, 0)), "bedtime")
        self.assertEqual(us.day_part(at(2, 0)), "bedtime")
        self.assertEqual(us.day_part(at(8, 0)), "day")

    def test_heads_up_at_night(self) -> None:
        bedtime = us.heads_up_message("1 minute", "in the bedroom", "2 hours", 9, part="bedtime")
        self.assertNotIn("somewhere new", bedtime)
        self.assertNotIn("change of scenery", bedtime)
        self.assertIn("Narrator extend time please", bedtime)
        evening = us.heads_up_message("1 minute", "in the kitchen", "2 hours", 8, part="evening")
        self.assertIn("if not, i'll pick a cozy spot inside.", evening)

    def test_evening_pick_stays_inside_and_cozy(self) -> None:
        self.kitchen_step()
        catalog = simulation.catalog_for(self.prefs["environment"])
        links = simulation.load_map()
        world = simulation.load_world(self.state())
        for start in ("bedroom", "kitchen-area", "backyard"):
            world["lora"]["location"] = start
            ctx = {"catalog": catalog, "links": links, "world": world, "wish": "", "late_night": False}
            ctx["allowed"], _ = simulation._candidates(ctx, random.Random(1))
            for seed in range(15):
                pick = simulation.walkable_pick(ctx, random.Random(seed), evening=True)
                self.assertIn(catalog["places"][pick]["zone"], simulation.INSIDE, (start, pick))
                self.assertTrue(simulation.can_move(catalog, links, start, pick)[0], (start, pick))

    def test_evening_move_after_the_limit_goes_inside(self) -> None:
        self.kitchen_step()
        self.now = at(19, 30)
        self.set_streak(us.EXTEND_LIMIT, at(19, 10))
        seen = {}

        def pick(ctx, rng, evening=False):
            seen["evening"] = evening
            return "living-room"

        with mock.patch.object(simulation, "walkable_pick", pick):
            simulation.advance_simulation(extend_at=int(time.time() * 1000), check_chat=False)
        self.assertTrue(seen["evening"])
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")

    def test_bedtime_extend_does_not_count_or_move_her(self) -> None:
        self.kitchen_step()
        self.now = at(21, 30)
        self.set_streak(us.EXTEND_LIMIT, at(20, 50))
        self.fake.plans = [plan(
            "kitchen-area",
            "lora sips chamomile tea at the kitchen table, Mochi yawning at her feet",
            bridge="lora asked to stay a little longer, so she made a warm cup of tea to wind down.",
        )]
        with mock.patch.object(simulation, "walkable_pick", side_effect=AssertionError("no moving at bedtime")):
            result = simulation.advance_simulation(extend_at=int(time.time() * 1000), check_chat=False)
        self.assertTrue(result["extended"])
        self.assertIn("It is bedtime, so the moment winds down", self.fake.prompts[-1])
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["extend_streak"], us.EXTEND_LIMIT)
        self.assertTrue(any("bedtime, so it does not count" in line for line in self.lines.lines))

    def test_the_count_starts_fresh_in_the_morning(self) -> None:
        self.kitchen_step()
        self.set_streak(6, at(20, 40))
        self.now = at(23, 0)
        self.assertEqual(simulation.here_summary(at(23, 0))["extends"], 6)
        self.assertEqual(simulation.here_summary(at(7, 59, day=25))["extends"], 6)
        self.assertEqual(simulation.here_summary(at(8, 30, day=25))["extends"], 0)
        self.now = at(9, 0, day=25)
        simulation.advance_simulation(extend_at=int(time.time() * 1000), check_chat=False)
        self.assertEqual(self.state()["world"]["extend_streak"], 1)


class HeadsUpTest(Base):
    def test_heads_up_message(self) -> None:
        message = us.heads_up_message("1 minute")
        self.assertEqual(
            message,
            "*heads up, lora: your surroundings will change automatically in 1 minute.*\n"
            'if you want to stay where you are a little longer, say "Narrator extend time please". '
            'if you would like to do something else, say "hey @narrator" and what you want to do or where you want to go. '
            'if you want to change the weather, please say "narrator change weather to per your request" basically, '
            "if you want me to change anything around you or your world. please tell me. "
            "if you dont like where you are at please tell me at the heads up when i ask you and ill move you to your specified area. "
            "I will check back every so often and ask you the last minute. please dont conversate with me.",
        )

    def test_heads_up_says_where_she_is_and_suggests_after_three_extends(self) -> None:
        two = us.heads_up_message("1 minute", "at the courtyard, strolling the paths", "45 minutes", 2)
        self.assertTrue(two.startswith(
            "*heads up, lora: you've been at the courtyard, strolling the paths for about 45 minutes "
            "(you've extended 2 times). your surroundings will change automatically in 1 minute.*\n"
        ))
        self.assertNotIn("stayed here a while", two)
        one = us.heads_up_message("1 minute", "at the courtyard", "20 minutes", 1)
        self.assertIn("(you've extended 1 time)", one)
        three = us.heads_up_message("1 minute", "at the courtyard", "1 hour 5 minutes", 3)
        self.assertIn(
            "\nyou've stayed here a while now. if you'd like a change of scenery, tell me with "
            '"hey @narrator" and where you want to go, or ask me to change something around you.\n',
            three,
        )
        self.assertIn('say "Narrator extend time please"', three)

    def test_narrator_understands_her_commands(self) -> None:
        def one(text, sender="ai", name=""):
            return us.narrator_command_from([{"timestamp": 1, "sender": sender, "display_name": name, "message": text}])[:3]

        self.assertEqual(one("Narrator extend time please. [looks back at Master]")[0], "extend")
        self.assertEqual(one("hey @narrator I want to go to the pond")[:1], ("go",))
        self.assertEqual(one("narrator change weather to light rain please"), ("weather", "Lora", "light rain"))
        self.assertEqual(one("[looks up] ...Narrator, change the weather to a thunderstorm."), ("weather", "Lora", "a thunderstorm"))
        self.assertEqual(one("narrator can you make it night"), ("change", "Lora", "make it night"))
        self.assertEqual(one("Narrator, turn the lights down a little."), ("change", "Lora", "turn the lights down a little"))
        self.assertEqual(one("narrator please add a hammock between the trees"), ("change", "Lora", "add a hammock between the trees"))
        for chatter in (
            "the narrator made it rain earlier",
            "i told the narrator about you",
            "Narrator... [sighs] you're annoying",
            "narrator change weather to per your request",
        ):
            self.assertEqual(one(chatter)[0], "", chatter)
        self.assertEqual(one(us.heads_up_message("1 minute"), "user", "Narrator")[0], "")

    def test_narrator_says_switches_back_to_whoever_was_chatting(self) -> None:
        self.fake.chat = [{"sender": "user", "display_name": "Mochi", "message": "hi lora", "timestamp": int(time.time() * 1000)}]
        reply, problem = us.narrator_says(us.heads_up_message("1 minute"))
        self.assertEqual(self.fake.profiles, ["Narrator", "Mochi"])
        self.assertEqual(self.fake.sent[-1][0], "message")
        self.assertIn("heads up, lora", self.fake.sent[-1][1])
        self.assertEqual(problem, "")

    def test_her_extend_reply_to_the_heads_up_is_found(self) -> None:
        messages = [
            {"timestamp": 1, "sender": "user", "display_name": "Narrator", "message": us.heads_up_message("1 minute")},
            {"timestamp": 2, "sender": "ai", "display_name": "", "message": "[looks up] ...Narrator extend time please."},
        ]
        self.assertEqual(us.narrator_command_from(messages)[0], "extend")
        self.assertEqual(us.narrator_command_from(messages[:1])[0], "")


DEFAULT_HEADS_UP = (
    "*heads up, lora: your surroundings will change automatically in 1 minute.*\n"
    'if you want to stay where you are a little longer, say "Narrator extend time please". '
    'if you would like to do something else, say "hey @narrator" and what you want to do or where you want to go. '
    'if you want to change the weather, please say "narrator change weather to per your request" basically, '
    "if you want me to change anything around you or your world. please tell me. "
    "if you dont like where you are at please tell me at the heads up when i ask you and ill move you to your specified area. "
    "I will check back every so often and ask you the last minute. please dont conversate with me."
)


class NarratorCommandsTest(Base):
    """The commands she can use come from the Narrator panel. The defaults work like before."""

    def said(self, text: str) -> tuple:
        return us.narrator_command_from([{"timestamp": 1, "sender": "ai", "display_name": "Lora", "message": text}])[:3]

    def use(self, commands: list[dict], message=None) -> None:
        prefs = us.load_prefs()
        prefs["narrator"] = {"commands": commands}
        if message is not None:
            prefs["narrator"]["message"] = message
        us.save_prefs(prefs)

    def test_the_default_commands_still_work(self) -> None:
        self.assertEqual(self.said("Narrator extend time please"), ("extend", "Lora", ""))
        self.assertEqual(self.said("narrator, extend my time"), ("extend", "Lora", ""))
        self.assertEqual(self.said("hey @narrator, take me to the orchard"), ("go", "Lora", "take me to the orchard"))
        self.assertEqual(self.said("Narrator take me home"), ("go", "Lora", "take me home"))
        self.assertEqual(self.said("narrator change weather to light snow please"), ("weather", "Lora", "light snow"))
        self.assertEqual(self.said("narrator change weather to per your request"), ("", "", ""))
        self.assertEqual(self.said("narrator make it night"), ("change", "Lora", "make it night"))
        self.assertEqual(self.said("narrator change the candles to dim"), ("change", "Lora", "change the candles to dim"))

    def test_a_new_command_of_each_kind(self) -> None:
        self.use([
            {"say": "Narrator I love it here", "does": "stay"},
            {"say": "narrator whisk me away to", "does": "go"},
            {"say": "narrator bring the", "does": "weather"},
            {"say": "narrator please light", "does": "change"},
            {"say": "narrator story time", "does": "custom", "what": "Master reads Lora a short story by the fire"},
        ])
        self.assertEqual(self.said("*sighs* Narrator, I love it here."), ("extend", "Lora", ""))
        self.assertEqual(self.said("narrator whisk me away to the mill pond."), ("go", "Lora", "whisk me away to the mill pond"))
        self.assertEqual(self.said("narrator bring the rain"), ("weather", "Lora", "rain"))
        self.assertEqual(self.said("narrator light a candle for me"), ("change", "Lora", "light a candle for me"))
        self.assertEqual(
            self.said("narrator story time, the one about the fox"),
            ("go", "Lora", "Master reads Lora a short story by the fire. the one about the fox"),
        )
        # The kinds she has no command for are not read at all.
        self.use([{"say": "Narrator I love it here", "does": "stay"}])
        self.assertEqual(self.said("hey @narrator take me to the orchard"), ("", "", ""))
        self.assertEqual(self.said("narrator make it night"), ("", "", ""))

    def test_mochi_repeating_the_heads_up_is_not_a_command(self) -> None:
        # Lora asked for the bedroom, then Mochi's wand repeated the heads-up with "Narrator extend time please" in it.
        chat = [
            {"timestamp": 1, "sender": "ai", "display_name": "Lora", "message": '"Hey @narrator, please take me to the bedroom."'},
            {"timestamp": 2, "sender": "user", "display_name": "Mochi", "message": 'Ruff! "Narrator extend time please"! Ruff!'},
            {"timestamp": 3, "sender": "user", "display_name": "Master", "message": DEFAULT_HEADS_UP},
        ]
        self.assertEqual(us.narrator_command_from(chat)[:3], ("go", "Lora", "please take me to the bedroom"))
        # Master can still give commands.
        chat.append({"timestamp": 4, "sender": "user", "display_name": "Master", "message": "narrator make it rain softly"})
        self.assertEqual(us.narrator_command_from(chat)[:3], ("change", "Master", "make it rain softly"))

    def test_the_heads_up_is_your_message(self) -> None:
        self.assertEqual(us.heads_up_message("1 minute"), DEFAULT_HEADS_UP)
        commands = [
            {"say": "Narrator I love it here", "does": "stay"},
            {"say": "narrator whisk me away to", "does": "go"},
        ]
        self.use(commands, message=(
            "psst, lora. {where}the scene changes in {wait}.\n"
            'to stay, say "Narrator I love it here".\n'
            'to go, say "narrator whisk me away to" and where.\n'
            "that's all."
        ))
        message = us.heads_up_message("1 minute", "at the well", "20 minutes")
        self.assertEqual(
            message,
            "*psst, lora. you've been at the well for about 20 minutes. the scene changes in 1 minute.*\n"
            'to stay, say "Narrator I love it here". to go, say "narrator whisk me away to" and where. that\'s all.',
        )
        # After many extends, going comes first, and the nudges and offers use her words for going.
        many = us.heads_up_message("1 minute", "at the well", "3 hours", 6, offer='just say "hey @narrator take me to the mill".')
        self.assertLess(many.index("to go, say"), many.index("to stay, say"))
        self.assertNotIn("hey @narrator", many)
        self.assertIn('"narrator whisk me away to take me to the mill"', many)
        self.assertNotIn("to stay, say", us.heads_up_message("1 minute", "at the well", "5 hours", us.EXTEND_LIMIT))
        self.assertIn("to stay", us.heads_up_message("1 minute", "at the well", "5 hours", us.EXTEND_LIMIT, part="bedtime"))

    def test_the_first_panel_version_still_loads(self) -> None:
        prefs = {"narrator": {"start": "hi lora.", "end": "bye.", "commands": [
            {"say": "Narrator hug time", "does": "custom", "what": "a hug", "tell": ""},
        ]}}
        found = narrator_commands.settings(prefs)
        self.assertEqual(found["message"], 'hi lora.\nyou can also say "Narrator hug time".\nbye.')

    def test_new_deleted_and_renamed_commands_update_the_message(self) -> None:
        message = narrator_commands.default_message()
        hug = {"say": "Narrator hug time", "does": "custom", "what": "Master hugs Lora"}
        message = narrator_commands.with_line_for(message, hug)
        lines = message.splitlines()
        self.assertEqual(lines[-2], 'you can also say "Narrator hug time".')
        self.assertTrue(lines[-1].startswith("if you dont like where you are"))
        weather = {"say": "narrator change weather to", "does": "weather", "what": ""}
        message = narrator_commands.without_lines_of(message, weather)
        self.assertNotIn("change the weather", message)
        self.assertIn("basically, if you want me to change anything", message)
        message = narrator_commands.renamed(message, "Narrator extend time please", "Narrator more time", [])
        self.assertIn('say "Narrator more time".', message)
        # "narrator change" starts "narrator change weather to", so renaming it leaves the weather words alone.
        text = 'say "narrator change weather to rain"'
        self.assertEqual(narrator_commands.renamed(text, "narrator change", "narrator make", ["narrator change weather to"]), text)

    def test_commands_that_would_go_wrong_are_not_saved(self) -> None:
        wrong = narrator_commands.problems({"commands": [
            {"say": "narrator", "does": "change"},
            {"say": "", "does": "stay"},
            {"say": "narrator hug me", "does": "custom", "what": ""},
            {"say": "Narrator, hug me!", "does": "change"},
        ]})
        self.assertEqual(len(wrong), 4)
        self.assertEqual(narrator_commands.problems(narrator_commands.defaults()), [])


class DiscoveryTest(Base):
    """DeepSeek may propose a new place. Python checks it, saves it, and keeps it connected."""

    def to_backyard(self) -> None:
        self.fake.plans = [plan("backyard", "lora hangs the bird feeder in the backyard by the fence, Mochi sniffing the garden hose")]
        simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")
        self.now = at(14, 10)
        self.fake.prompts.clear()

    def discover_conservatory(self) -> dict:
        self.to_backyard()
        self.fake.plans = [discover()]
        return simulation.advance_simulation()

    def test_existing_place_needs_no_discovery(self) -> None:
        self.fake.plans = [plan("kitchen-area", "lora rinses berries at the kitchen sink, Mochi watching the oven light", new_location=None)]
        simulation.advance_simulation()
        world = self.state()["world"]
        self.assertEqual(world["lora"]["location"], "kitchen-area")
        self.assertEqual(world["discovered"], {})
        self.assertIn('"new_location": null', self.fake.prompts[0])

    def test_valid_new_place_is_accepted_and_saved(self) -> None:
        result = self.discover_conservatory()
        world = self.state()["world"]
        place = world["discovered"]["hidden-glass-conservatory"]
        self.assertEqual(world["lora"]["location"], "hidden-glass-conservatory")
        self.assertEqual(world["mochi"]["location"], "hidden-glass-conservatory")
        self.assertEqual((place["zone"], place["from"]), ("indoor", "backyard"))
        self.assertEqual(place["items"], ["ferns", "wicker chair", "glass roof"])
        self.assertEqual(result["discovered"], "Hidden glass conservatory")
        self.assertEqual(result["location"], "Hidden glass conservatory")
        self.assertEqual(world["journal"][-1]["from"], "backyard")
        self.assertTrue(any(item["kind"] == "discovery" and item["importance"] == 3 for item in world["memories"]))
        self.assertEqual(self.state()["prefs"]["environment"], self.prefs["environment"])
        self.assertIn(("message", result["message"]), self.fake.sent)

    def test_discovered_place_survives_restart(self) -> None:
        self.discover_conservatory()
        simulation._catalog_cache.clear()
        info = simulation.summary()
        self.assertEqual(info["place"], "Hidden glass conservatory (indoor, discovered)")
        self.assertEqual(info["discovered"], ["Hidden glass conservatory"])
        saved = simulation.load_world(us.read_state())
        catalog = simulation.catalog_for(self.prefs["environment"], saved["discovered"])
        self.assertEqual(catalog["places"]["hidden-glass-conservatory"]["origin"], "discovered")
        self.assertNotIn("hidden-glass-conservatory", simulation.catalog_for(self.prefs["environment"])["places"])
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")

    def test_invalid_new_places_are_rejected(self) -> None:
        self.to_backyard()
        self.fake.plans = [
            discover(name="X!"),
            discover(type="space"),
            discover(name="Glowing portal room", description="A room with a portal to another planet."),
            discover(description="glass"),
            discover(objects="ferns"),
            plan("tool-shed", "lora sorts garden gloves in the tool shed, Mochi sniffing a bucket"),
        ]
        with mock.patch.object(simulation, "PLAN_TRIES", 6):
            simulation.advance_simulation()
        reasons = "\n".join(self.fake.prompts[1:])
        for reason in ("plain name", "indoor or outdoor", "does not fit her world", "one-sentence description", "list of up to 6"):
            self.assertIn(reason, reasons)
        self.assertEqual(self.state()["world"]["discovered"], {})

    def test_duplicate_new_place_is_rejected(self) -> None:
        self.discover_conservatory()
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.now = at(14, 30)
        self.fake.prompts.clear()
        self.fake.plans = [
            discover(name="Greenhouse", type="outdoor"),
            discover(),
            plan("tool-shed", "lora sorts garden gloves in the tool shed, Mochi sniffing a bucket"),
        ]
        simulation.advance_simulation()
        self.assertIn("already exists in her world as the greenhouse", self.fake.prompts[1])
        self.assertIn("already exists in her world as the hidden glass conservatory", self.fake.prompts[2])
        self.assertEqual(list(self.state()["world"]["discovered"]), ["hidden-glass-conservatory"])

    def test_impossible_connection_is_rejected(self) -> None:
        self.to_backyard()
        self.fake.plans = [
            discover(connection_from="kitchen-area"),
            discover(connection_from="moon-base"),
            plan("tool-shed", "lora sorts garden gloves in the tool shed, Mochi sniffing a bucket"),
        ]
        simulation.advance_simulation()
        self.assertIn('has to connect to where she is now, "backyard"', self.fake.prompts[1])
        self.assertIn('"connection_from" has to be a place in her world', self.fake.prompts[2])
        self.assertEqual(self.state()["world"]["discovered"], {})

    def test_no_teleport_to_or_from_discovered_place(self) -> None:
        self.discover_conservatory()
        world = simulation.load_world(us.read_state())
        catalog = simulation.catalog_for(self.prefs["environment"], world["discovered"])
        links = simulation.load_map()
        self.assertTrue(simulation.can_move(catalog, links, "backyard", "hidden-glass-conservatory")[0])
        self.assertFalse(simulation.can_move(catalog, links, "fireplace", "hidden-glass-conservatory")[0])
        self.assertFalse(simulation.can_move(catalog, links, "kitchen-area", "hidden-glass-conservatory")[0])
        self.assertFalse(simulation.can_move(catalog, links, "hidden-glass-conservatory", "fireplace")[0])
        self.now = at(14, 20)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("kitchen-area", "lora washes the fern pot at the kitchen sink, Mochi by the toaster"),
            plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind"),
        ]
        simulation.advance_simulation()
        self.assertIn("she can only go back to the backyard", self.fake.prompts[1])
        self.assertEqual(self.state()["world"]["lora"]["location"], "backyard")

    def test_no_discovery_late_at_night_or_past_the_daily_limit(self) -> None:
        self.now = at(23, 0)
        self.fake.plans = [
            discover(connection_from="fireplace"),
            plan("floor-rug", "lora curls up on the floor rug with Mochi, curtains drawn, the room quiet and dark"),
        ]
        simulation.advance_simulation()
        self.assertIn('"new_location" is null.', self.fake.prompts[0])
        self.assertIn("late at night", self.fake.prompts[1])
        world = {"discovered": {f"place-{n}": {"discovered_at": simulation.iso(at(9 + n))} for n in range(4)}}
        self.assertIn("already found 4 new places today", simulation.discovery_block(world, at(14), False, ""))

    def test_failed_deepseek_keeps_discovered_world(self) -> None:
        self.discover_conservatory()
        before = self.digest()
        self.now = at(14, 20)
        self.fake.plans = [RuntimeError("DeepSeek returned 503")] * simulation.PLAN_TRIES
        with self.assertRaises(RuntimeError):
            simulation.advance_simulation()
        self.assertEqual(self.digest(), before)
        self.assertIn("hidden-glass-conservatory", self.state()["world"]["discovered"])

    def test_failed_kindroid_keeps_discovery_and_retries(self) -> None:
        self.to_backyard()
        self.fake.fail_tell = True
        self.fake.plans = [discover()]
        result = simulation.advance_simulation()
        world = self.state()["world"]
        self.assertTrue(result["pending"])
        self.assertIn("hidden-glass-conservatory", world["discovered"])
        self.assertEqual(world["lora"]["location"], "hidden-glass-conservatory")
        self.fake.fail_tell = False
        self.fake.sent.clear()
        retry = simulation.advance_simulation()
        self.assertTrue(retry["delivered"])
        self.assertEqual(self.fake.sent, [("message", result["message"])])
        self.assertIn("hidden-glass-conservatory", self.state()["world"]["discovered"])

    def test_request_cannot_jump_to_an_unreachable_discovered_place(self) -> None:
        self.discover_conservatory()
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.now = at(14, 30)
        self.fake.plans = [plan("fireplace", "lora sets the fern pot by the fireplace tools, Mochi sniffing the wood logs")]
        simulation.advance_simulation()
        self.now = at(14, 35)
        self.fake.plans = [plan("kitchen-area", "lora rinses the fern pot at the kitchen sink, Mochi by the toaster")]
        simulation.advance_simulation()
        # From the kitchen the backyard is not one walk away, so neither is the conservatory.
        self.now = at(14, 40)
        self.fake.prompts.clear()
        self.fake.plans = [
            plan("hidden-glass-conservatory", INTO_CONSERVATORY),
            plan("living-room", "lora sets the fern pot on the living room windowsill, Mochi on the sofa"),
        ]
        simulation.advance_simulation(request="take me to the hidden glass conservatory", requested_by="Lora")
        self.assertIn("can only be reached from the backyard", self.fake.prompts[1])
        self.assertEqual(self.state()["world"]["lora"]["location"], "living-room")

    def test_a_discovered_place_can_be_reached_by_walking_past_where_it_was_found(self) -> None:
        self.discover_conservatory()
        self.now = at(14, 20)
        self.fake.plans = [plan("backyard", "lora carries a fern pot out to the backyard picnic table, Mochi trotting behind")]
        simulation.advance_simulation()
        self.now = at(14, 30)
        self.fake.plans = [plan("fireplace", "lora sets the fern pot by the fireplace tools, Mochi sniffing the wood logs")]
        simulation.advance_simulation()
        self.now = at(14, 40)
        self.fake.plans = [plan("hidden-glass-conservatory", INTO_CONSERVATORY)]
        simulation.advance_simulation(request="take me to the hidden glass conservatory", requested_by="Lora")
        self.assertEqual(self.state()["world"]["lora"]["location"], "hidden-glass-conservatory")

    def test_old_saved_world_without_discovered_still_loads(self) -> None:
        self.fake.plans = [plan("kitchen-area", "lora rinses berries at the kitchen sink, Mochi watching the oven light")]
        simulation.advance_simulation()
        state = self.state()
        del state["world"]["discovered"]
        (self.tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(simulation.summary()["discovered"], [])
        self.now = at(14, 10)
        self.fake.plans = [plan("living-room", "lora folds blankets on the living room sofa, Mochi chewing a cushion tassel")]
        simulation.advance_simulation()
        self.assertEqual(self.state()["world"]["discovered"], {})


class InlineThread:
    """Tk callbacks need the main loop; without it, run the worker on the test thread."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self) -> None:
        self.target(*self.args, **self.kwargs)


class GuiTest(Base):
    """Change action runs a step, Stop keeps it stopped, and a restart resumes the timer."""

    def setUp(self) -> None:
        super().setUp()
        import gui

        self.gui = gui
        self.calls: list[dict] = []
        slides = self.tmp / "slides"
        slides.mkdir()
        self.patches += [
            mock.patch.object(gui, "SLIDES_DIR", slides),
            mock.patch.object(simulation, "advance_simulation", self.fake_step),
            mock.patch.object(gui, "threading", types.SimpleNamespace(Thread=InlineThread)),
            mock.patch.object(simulation, "discovery_offer", lambda *args, **kwargs: ""),
        ]
        for patch in self.patches[-4:]:
            patch.start()

    def fake_step(
        self,
        prior="",
        environment="",
        backstory="",
        morning=False,
        request="",
        requested_by="",
        dry_run=False,
        check_chat=True,
        **_kwargs,
    ):
        self.calls.append(
            {
                "prior": prior,
                "morning": morning,
                "extend_at": _kwargs.get("extend_at", 0),
                "request": request,
                "weather": _kwargs.get("weather", ""),
                "stay_here": _kwargs.get("stay_here", False),
            }
        )
        return {"setting": "lora plays tug with Mochi in the open field", "pending": False, "error": "", "delivered": False}

    def pump(self, app, until) -> None:
        for _ in range(200):
            app.update()
            if until():
                return
            time.sleep(0.01)

    def test_change_action_and_stop(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.start()
            self.pump(app, lambda: app.phase == "waiting")
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(simulation.schedule()["running"])
            self.assertEqual(app.prior.get("1.0", "end").strip(), "lora plays tug with Mochi in the open field")
            app.stop()
            self.assertEqual(app.phase, "idle")
            self.assertFalse(simulation.schedule()["running"])
            app.deadline = time.monotonic() - 1
            app.clock()
            app.update()
            self.assertEqual(len(self.calls), 1)
        finally:
            app.destroy()

    def test_picture_has_no_boxes_behind_labels(self) -> None:
        from PIL import Image

        source = self.tmp / "slides" / "001.png"
        Image.new("RGB", (400, 300), (200, 100, 50)).save(source)
        app = self.gui.App()
        app.withdraw()
        try:
            app._ensure_plate()
            app.opacity_value = 0
            app._draw_plate(source, 300, 200)
            photo = app._plate_photo._PhotoImage__photo
            center = tuple(int(part) for part in re.findall(r"\d+", str(photo.get(150, 100))))
            edge = tuple(int(part) for part in re.findall(r"\d+", str(photo.get(4, 4))))
            self.assertEqual(center, (200, 100, 50))
            self.assertNotEqual(edge, center)
        finally:
            app.destroy()

    def test_click_on_picture_drags_the_window(self) -> None:
        app = self.gui.App()
        app.withdraw()
        grabbed: list[int] = []
        try:
            app._ensure_plate()
            self.assertIn("_grab_from_picture", app.plate_label.bind("<ButtonPress-1>"))
            with mock.patch.object(self.gui, "drag_window", grabbed.append):
                app._grab_from_picture()
            main = self.gui.ctypes.windll.user32.GetAncestor(app.winfo_id(), 2)
            self.assertEqual(grabbed, [main])
        finally:
            app.destroy()

    def test_drag_window_hands_the_press_to_the_title_bar(self) -> None:
        calls: list[tuple] = []

        class Send:
            def __call__(self, *args):
                calls.append(("send", *args))

        def cursor(point):
            point._obj.x, point._obj.y = -20, 300

        user32 = types.SimpleNamespace(
            GetCursorPos=cursor,
            SetForegroundWindow=lambda hwnd: calls.append(("front", hwnd.value)),
            ReleaseCapture=lambda: calls.append(("release",)),
            SendMessageW=Send(),
        )
        fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=user32), byref=ctypes.byref, c_ssize_t=ctypes.c_ssize_t)
        with mock.patch.object(self.gui, "ctypes", fake):
            self.gui.drag_window(1234)
        self.assertEqual(calls, [("front", 1234), ("release",), ("send", 1234, 0x00A1, 2, (300 << 16) | 0xFFEC)])

    def test_peek_reads_every_page_since_the_last_step(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            step_start = int(time.time() * 1000)
            filler = [
                {"timestamp": step_start + i, "sender": "ai", "display_name": "Lora", "message": f"line {i}"}
                for i in range(20)
            ]
            ask = {
                "timestamp": step_start + 100,
                "sender": "ai",
                "display_name": "Lora",
                "message": "Hey @narrator, take me to the treehouse",
            }
            seen: list[int] = []

            def fetch(after, group_id=None):
                seen.append(after)
                return (filler, 0) if len(seen) == 1 else ([ask], 0)

            with mock.patch.object(simulation, "last_step_ms", lambda: step_start), mock.patch.object(us, "fetch_messages", fetch):
                app.arm_timer(app.read_options(), delay=600)
                app._peek_narrator()
                app.update()
            self.assertEqual(seen[0], step_start)
            self.assertEqual(app.narrator_request, "take me to the treehouse")
        finally:
            app.destroy()

    def test_one_window_with_start_settings_and_help_pages(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.update()
            self.assertEqual(app.page, "home")
            self.assertTrue(app.ask_button.winfo_ismapped() or app.ask_button.grid_info())
            self.assertFalse(app.settings_page.grid_info())
            app._nav()  # Settings
            self.assertEqual((app.page, app.drop_open), ("settings", "Schedule"))
            self.assertTrue(app.settings_page.grid_info())
            self.assertFalse(app.prior.grid_info())
            self.assertEqual(app.nav_button.cget("text"), "← Back")
            app.toggle_drop("Narrator")
            self.assertTrue(app.drop_panels["Narrator"].grid_info())
            self.assertFalse(app.drop_panels["Schedule"].grid_info())
            app.open_help()
            self.assertEqual(app.page, "help")
            self.assertFalse(app.settings_page.grid_info())
            app._nav()  # Back
            self.assertEqual(app.page, "home")
            self.assertTrue(app.prior.grid_info())
            self.assertEqual(app.nav_button.cget("text"), "Settings")
            self.assertEqual([w for w in app.winfo_children() if isinstance(w, self.gui.tk.Toplevel)], [])
        finally:
            app.destroy()

    def test_the_mouse_wheel_scrolls_settings(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.toggle_drop("Narrator")
            app.geometry("600x420")
            app.update()
            app._settings_scrolled()
            app.update()
            self.assertTrue(app.settings_bar.winfo_ismapped() or app.settings_bar.grid_info())
            with mock.patch.object(app.settings_bar, "winfo_ismapped", return_value=True):
                event = types.SimpleNamespace(widget=app.drop_panels["Narrator"], delta=-120)
                app._on_wheel(event)
                app.update()
            self.assertGreater(app.settings_canvas.yview()[0], 0)
            app._on_wheel(types.SimpleNamespace(widget=app.drop_panels["Narrator"], delta=120))
        finally:
            app.destroy()

    def test_every_option_has_a_hover_tip(self) -> None:
        import help_guide

        app = self.gui.App()
        app.withdraw()
        try:
            named = {cast.localize(key) for key in help_guide.TIPS} | set(help_guide.TIPS)
            options = [
                widget for widget in app._each_widget(everywhere=True)
                if widget.winfo_class() in ("Button", "Checkbutton", "Label") and str(widget.cget("text")).strip() in named
            ]
            self.assertGreater(len(options), 40)
            missing = [str(widget.cget("text")) for widget in options if not getattr(widget, "_has_tip", False)]
            self.assertEqual(missing, [])
            for label in ("Start", "Stop", "Sleep", "Settings", "Change every", "Wake at", "Suggest places", "Wipe everything and start over"):
                self.assertIn(cast.localize(label), {str(widget.cget("text")) for widget in options}, label)
        finally:
            app.destroy()

    def test_text_grows_with_the_window_and_shrinks_back(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            font = self.gui.tkfont.Font(font=app.ask_button.cget("font"))
            normal = font.actual("size")
            with mock.patch.object(app, "winfo_width", return_value=1900), mock.patch.object(app, "winfo_height", return_value=1050):
                app._rescale(app)
            self.assertEqual(app.text_scale(app), self.gui.MAX_TEXT_SCALE)
            self.assertGreater(self.gui.tkfont.Font(font=app.ask_button.cget("font")).actual("size"), normal * 2)
            with mock.patch.object(app, "winfo_width", return_value=560), mock.patch.object(app, "winfo_height", return_value=400):
                app._rescale(app)
            self.assertEqual(app.text_scale(app), 1.0)
            self.assertEqual(self.gui.tkfont.Font(font=app.ask_button.cget("font")).actual("size"), normal)
        finally:
            app.destroy()

    def test_the_help_bubble_asks_deepseek_about_the_app(self) -> None:
        import help_guide

        sent = []

        def post(url, payload, headers, timeout, required=False):
            sent.append(payload)
            return {"choices": [{"message": {"content": "Press Start. The Narrator warns Lora first."}}]}

        app = self.gui.App()
        app.withdraw()
        try:
            with mock.patch.object(us, "post_json", post), mock.patch.dict(us.os.environ, {"DEEPSEEK_API_KEY": "dk"}), \
                    mock.patch.object(us, "load_env", lambda path: None):
                app.open_help()
                self.assertEqual(app.page, "help")
                app.help_win.entry.insert(0, "How do I start?")
                app.help_win.send()
                app.update()
            chat = app.help_win.chat.get("1.0", "end")
            self.assertIn("You\nHow do I start?", chat)
            self.assertIn("Helper\nPress Start. The Narrator warns Lora first.", chat)
            self.assertNotIn("Thinking...", chat)
            # Markdown from DeepSeek shows as plain lines.
            self.assertEqual(self.gui.tidy_answer("**Steps:**\n\n1. Press **Start**.\n\n* Wait"), ["Steps:", "1. Press Start.", "- Wait"])
            system = sent[0]["messages"][0]["content"]
            self.assertIn("THE GUIDE", system)
            self.assertIn("Warn Lora 1 minute before each change", system)
            self.assertEqual(sent[0]["messages"][-1], {"role": "user", "content": "How do I start?"})
            # Without a key it says where to add one.
            with mock.patch.dict(us.os.environ, {"DEEPSEEK_API_KEY": ""}), mock.patch.object(us, "load_env", lambda path: None):
                with self.assertRaises(RuntimeError) as caught:
                    help_guide.ask([{"role": "user", "content": "hi"}])
            self.assertIn("Change setup", str(caught.exception))
        finally:
            app.destroy()

    def test_error_while_idle_shows_the_window(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            self.assertTrue(app.countdown.grid_info())  # the full window from the start, no picture-only view
            app.times.delete(0, "end")
            app.times.insert(0, "25:00")
            app.start()
            app.update()
            self.assertTrue(app.countdown.grid_info())
            self.assertEqual(app.countdown.cget("text"), "Use times like 10:00 PM.")
        finally:
            app.destroy()

    def test_heads_up_one_minute_before_then_the_chat_is_read(self) -> None:
        said = []
        order = []

        def narrator_says(message):
            said.append(message)
            order.append("heads-up")
            return '[looks up] ...Narrator extend time please.', ""

        with mock.patch.object(self.gui.update_scene, "narrator_says", narrator_says), mock.patch.object(
            self.gui.update_scene, "recent_messages", lambda after, pages=3: order.append("read") or []
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                now = us.la_now()
                app.deadline = time.monotonic() + 120
                app.follow_schedule(now)
                self.assertEqual(said, [])
                app.deadline = time.monotonic() + 59
                app.follow_schedule(now)
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertEqual(len(said), 1)
                self.assertIn("will change automatically in 1 minute.", said[0])
                self.assertIn('say "Narrator extend time please"', said[0])
                self.assertIn('say "hey @narrator"', said[0])
                self.assertIn("please dont conversate with me.", said[0])
                self.assertFalse(app.narrator_peeked)
                app.deadline = time.monotonic() + 29
                app.follow_schedule(now)
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertEqual(order[:2], ["heads-up", "read"])
                app.follow_schedule(now)
                self.assertEqual(len(said), 1)
            finally:
                app.destroy()

    def test_second_extend_in_the_heads_up_answer_is_not_lost(self) -> None:
        """10:21 AM: her first extend was already used, and 40 minutes of chat hid the new one."""
        used = int(time.time() * 1000) - 20 * 60 * 1000
        old_chat = [
            {"timestamp": used - 1000 + index, "sender": "user", "display_name": "Master", "message": f"line {index}"}
            for index in range(59)
        ] + [{"timestamp": used, "sender": "ai", "display_name": "", "message": "Narrator extend time please."}]

        with mock.patch.object(
            self.gui.update_scene, "narrator_says",
            lambda message: ("Narrator extend time please. [looks back at Master] ...I don't want you to disappear.", ""),
        ), mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(old_chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: used
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                now = us.la_now()
                app.deadline = time.monotonic() + 59
                app.follow_schedule(now)
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertGreater(app.narrator_extend_at, used)
                app.deadline = time.monotonic() + 29
                app.follow_schedule(now)
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertGreater(app.narrator_extend_at, used)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(now)
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(len(self.calls), 1)
                self.assertGreater(self.calls[0]["extend_at"], used)
            finally:
                app.destroy()

    def test_an_extend_that_was_already_used_is_not_taken_again(self) -> None:
        used = int(time.time() * 1000) - 60_000
        with mock.patch.object(simulation, "last_extend_ms", lambda: used):
            app = self.gui.App()
            app.withdraw()
            try:
                app.arm_timer(app.read_options(), delay=600)
                app._take_command(("extend", "lora", "", used))
                self.assertEqual(app.narrator_extend_at, 0)
                app._take_command(("go", "lora", "the pond", used + 5))
                app._take_command(("extend", "lora", "", used + 1))
                self.assertEqual(app.narrator_request, "the pond")
                self.assertEqual(app.narrator_extend_at, 0)
            finally:
                app.destroy()

    def test_mid_wait_check_does_her_weather_command_right_away(self) -> None:
        stamp = int(time.time() * 1000)
        chat = [{"timestamp": stamp, "sender": "ai", "display_name": "", "message": "[looks at the sky] narrator change weather to light rain"}]
        with mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.arm_timer(app.read_options(), delay=20 * 60)
                app.deadline = time.monotonic() + 15 * 60
                app.follow_schedule(us.la_now())
                self.assertEqual(self.calls, [])
                app.next_mid_check = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(self.calls[0]["weather"], "light rain")
            finally:
                app.destroy()

    def test_mid_wait_extend_waits_for_the_end(self) -> None:
        stamp = int(time.time() * 1000)
        chat = [{"timestamp": stamp, "sender": "ai", "display_name": "", "message": "Narrator extend time please."}]
        with mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(False)
                app.arm_timer(app.read_options(), delay=20 * 60)
                app.deadline = time.monotonic() + 15 * 60
                app.next_mid_check = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertEqual(self.calls, [])
                self.assertEqual(app.narrator_extend_at, stamp)
                app.deadline = time.monotonic() + 50
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(self.calls[0]["extend_at"], stamp)
            finally:
                app.destroy()

    def test_change_action_gives_the_heads_up_first_then_follows_her_request(self) -> None:
        said = []

        def narrator_says(message):
            said.append(message)
            return "hey @narrator I want to go sit on the porch swing with Mochi, my arms are tired", ""

        with mock.patch.object(self.gui.update_scene, "narrator_says", narrator_says), mock.patch.object(
            self.gui.update_scene, "recent_messages", lambda after, pages=3: []
        ), mock.patch.object(simulation, "last_extend_ms", lambda: 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.start()
                self.assertEqual(self.calls, [])
                self.assertEqual(app.phase, "waiting")
                self.assertAlmostEqual(app.deadline - time.monotonic(), 60, delta=3)
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertEqual(len(said), 1)
                self.assertIn("will change automatically in 1 minute.", said[0])
                self.assertEqual(self.calls, [])
                app.deadline = time.monotonic() + 29
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(self.calls[0]["request"], "I want to go sit on the porch swing with Mochi, my arms are tired")
            finally:
                app.destroy()

    def test_change_action_is_instant_when_the_heads_up_is_off(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.heads_up.set(False)
            app.start()
            self.pump(app, lambda: len(self.calls) == 1)
            self.assertEqual(len(self.calls), 1)
        finally:
            app.destroy()

    def test_no_change_until_the_heads_up_gets_through(self) -> None:
        """12:11 PM: the internet dropped, the heads-up failed, and the change went ahead anyway."""
        tries = []

        def narrator_says(message):
            tries.append(message)
            if len(tries) == 1:
                raise RuntimeError("Could not reach https://api.kindroid.ai/v1/update-info: [Errno 11001] getaddrinfo failed")
            return "[looks up] mm, okay", ""

        with mock.patch.object(self.gui.update_scene, "narrator_says", narrator_says), mock.patch.object(
            self.gui.update_scene, "recent_messages", lambda after, pages=3: []
        ), mock.patch.object(simulation, "last_extend_ms", lambda: 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 59
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertEqual(len(tries), 1)
                self.assertGreater(app.deadline - time.monotonic(), 80)
                app.deadline = time.monotonic() + 25
                app.follow_schedule(us.la_now())
                self.assertEqual(self.calls, [])
                self.assertEqual(len(tries), 2)
                self.pump(app, lambda: not app.heads_up_busy)
                app.deadline = time.monotonic() + 20
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.answer_until = time.monotonic() - 1  # "mm, okay" has no command: skip the wait for her answer
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(len(self.calls), 1)
            finally:
                app.destroy()

    def test_heads_up_carries_the_place_offer(self) -> None:
        said = []
        offer = 'by the way, lora: you notice a path. if you\'d like to go see it, just say "hey @narrator take me to the path".'
        with mock.patch.object(
            self.gui.update_scene, "narrator_says", lambda message: said.append(message) or ("Narrator extend time please.", "")
        ), mock.patch.object(simulation, "discovery_offer", lambda environment="", now=None: offer):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 59
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertEqual(len(said), 1)
                self.assertIn(offer, said[0])
            finally:
                app.destroy()

    def code_folder(self) -> Path:
        folder = self.tmp / "app"
        folder.mkdir()
        for name in self.gui.CODE_FILES:
            (folder / name).write_text("x = 1\n", encoding="utf-8")
        return folder

    def test_restarts_after_an_update_once_the_files_settle(self) -> None:
        folder = self.code_folder()
        restarts = []
        with mock.patch.object(self.gui, "APP_DIR", folder), mock.patch.object(self.gui, "UPDATE_SETTLE_SECONDS", 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.code_seen = self.gui.code_stamp()
                app.restart_app = lambda: restarts.append(True)

                def check():
                    app.next_update_check = 0
                    app._check_for_update()

                check()
                self.assertEqual(restarts, [])
                (folder / "simulation.py").write_text("x = 2  # updated\n", encoding="utf-8")
                check()  # sees the change, waits for it to settle
                self.assertEqual(restarts, [])
                check()
                self.assertEqual(restarts, [True])
            finally:
                app.destroy()

    def test_no_restart_in_the_middle_of_things_or_with_a_broken_update(self) -> None:
        folder = self.code_folder()
        restarts = []
        with mock.patch.object(self.gui, "APP_DIR", folder), mock.patch.object(self.gui, "UPDATE_SETTLE_SECONDS", 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.code_seen = self.gui.code_stamp()
                app.restart_app = lambda: restarts.append(True)

                def check():
                    app.next_update_check = 0
                    app._check_for_update()

                (folder / "gui.py").write_text("def broken(:\n", encoding="utf-8")
                check()
                check()
                self.assertEqual(restarts, [])
                self.assertTrue(any("the update has a mistake" in line for line in self.lines.lines))
                (folder / "gui.py").write_text("x = 3\n", encoding="utf-8")
                check()
                app.phase = "waiting"
                app.deadline = time.monotonic() + 60
                check()
                self.assertEqual(restarts, [])
                self.assertTrue(any("It restarts as soon as" in line for line in self.lines.lines))
                app.heads_up_busy = True
                app.deadline = time.monotonic() + 600
                check()
                self.assertEqual(restarts, [])
                app.heads_up_busy = False
                check()
                self.assertEqual(restarts, [True])
                app.phase = "idle"
            finally:
                app.destroy()

    def test_restart_starts_the_new_version_then_closes(self) -> None:
        started = []
        with mock.patch.object(self.gui.subprocess, "Popen", lambda args, **kwargs: started.append((args, kwargs))):
            app = self.gui.App()
            app.withdraw()
            closed = []
            app._close = lambda: closed.append(True)
            try:
                app.restart_app()
                self.assertEqual(started[0][0][1], str(self.gui.APP_DIR / "gui.py"))
                self.assertEqual(started[0][1]["cwd"], str(self.gui.APP_DIR))
                self.assertEqual(closed, [True])
            finally:
                app.destroy()

    def test_a_change_due_after_a_restart_still_gets_the_heads_up(self) -> None:
        simulation.set_schedule(True, us.la_now() - timedelta(minutes=5))
        app = self.gui.App()
        app.withdraw()
        try:
            app.heads_up.set(True)
            app._resume_schedule()
            self.assertEqual(app.phase, "waiting")
            self.assertTrue(app.start_heads_up)
            self.assertTrue(app._heads_up_on())
        finally:
            app.stop()
            app.destroy()

    def test_an_ignored_heads_up_waits_for_her_answer(self) -> None:
        """5:41 PM: she ignored the heads-up, Master said "answer narrator lora", and her extend came after the change."""
        chat: list[dict] = []
        reads = []

        def recent(after, pages=3):
            reads.append(after)
            return [item for item in chat if item["timestamp"] > after]

        with mock.patch.object(
            self.gui.update_scene, "narrator_says",
            lambda message: ("[ignores the announcement, keeping my eyes on Master] ...We're just... sitting here.", ""),
        ), mock.patch.object(self.gui.update_scene, "recent_messages", recent), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 59
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertTrue(app.awaiting_answer)
                self.assertTrue(any("The change waits up to 2 more minutes" in line for line in self.lines.lines))
                app.deadline = time.monotonic() + 29
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                # The minute is up, but she has not answered: no change yet, and the chat is read again.
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertEqual(self.calls, [])
                self.assertGreaterEqual(reads[-1], app.heads_up_ms)
                now_ms = int(time.time() * 1000)
                chat.extend([
                    {"timestamp": now_ms, "sender": "user", "display_name": "Master", "message": "answer narrator lora"},
                    {"timestamp": now_ms + 1, "sender": "ai", "display_name": "", "message": "[flinches] ...Narrator extend time please."},
                ])
                app.next_answer_check = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0]["extend_at"], now_ms + 1)
            finally:
                app.destroy()

    def test_when_she_never_answers_the_change_goes_ahead(self) -> None:
        with mock.patch.object(
            self.gui.update_scene, "narrator_says", lambda message: ("[keeps talking to Master]", "")
        ), mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: []), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 59
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                app.deadline = time.monotonic() + 29
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                self.assertEqual(self.calls, [])
                app.answer_until = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0]["extend_at"], 0)
                self.assertTrue(any("didn't answer the heads-up, so the change goes ahead" in line for line in self.lines.lines))
            finally:
                app.destroy()

    def test_after_ten_failed_heads_ups_the_change_goes_ahead(self) -> None:
        def narrator_says(message):
            raise RuntimeError("getaddrinfo failed")

        with mock.patch.object(self.gui.update_scene, "narrator_says", narrator_says), mock.patch.object(
            self.gui.update_scene, "recent_messages", lambda after, pages=3: []
        ), mock.patch.object(simulation, "last_extend_ms", lambda: 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                for _ in range(self.gui.HEADS_UP_TRIES + 1):
                    app.deadline = time.monotonic() + 59
                    app.follow_schedule(us.la_now())
                    self.pump(app, lambda: not app.heads_up_busy)
                app.deadline = time.monotonic() + 20
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.deadline = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertEqual(len(self.calls), 1)
            finally:
                app.destroy()

    def test_the_request_behind_the_last_change_is_not_read_again(self) -> None:
        """11:56 AM: the 11:45 sunflower request was read again and caused an early change."""
        step = int(time.time() * 1000) - 10 * 60 * 1000
        with mock.patch.object(simulation, "last_step_ms", lambda: step), mock.patch.object(simulation, "last_extend_ms", lambda: 0):
            app = self.gui.App()
            app.withdraw()
            try:
                app.arm_timer(app.read_options(), delay=15 * 60)
                self.assertEqual(app.narrator_after, step)
            finally:
                app.destroy()

    def test_a_check_in_never_changes_anything_for_a_finished_request(self) -> None:
        stamp = int(time.time() * 1000)
        chat = [{"timestamp": stamp, "sender": "ai", "display_name": "", "message": "hey @narrator take me to the sunflower field"}]
        with mock.patch.object(self.gui.update_scene, "recent_messages", lambda after, pages=3: list(chat)), mock.patch.object(
            simulation, "last_extend_ms", lambda: 0
        ), mock.patch.object(simulation, "request_is_new", lambda text: False):
            app = self.gui.App()
            app.withdraw()
            try:
                app.arm_timer(app.read_options(), delay=20 * 60)
                app.deadline = time.monotonic() + 15 * 60
                app.next_mid_check = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.narrator_peeking)
                app.update()
                self.assertEqual(self.calls, [])
                self.assertEqual(app.phase, "waiting")
            finally:
                app.destroy()

    def test_heads_up_from_the_app_says_where_she_is(self) -> None:
        said = []
        here = {"where": "at the courtyard, strolling the paths", "how_long": "1 hour", "extends": 3}
        with mock.patch.object(self.gui.update_scene, "narrator_says", lambda message: said.append(message) or ("", "")), mock.patch.object(
            simulation, "here_summary", lambda now=None: here
        ):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 59
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: not app.heads_up_busy)
                self.assertIn("you've been at the courtyard, strolling the paths for about 1 hour (you've extended 3 times).", said[0])
                self.assertIn("you've stayed here a while now.", said[0])
            finally:
                app.destroy()

    def test_no_heads_up_when_switched_off_or_the_action_is_short(self) -> None:
        said = []
        with mock.patch.object(self.gui.update_scene, "narrator_says", lambda message: said.append(message) or ("", "")):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(False)
                app.arm_timer(app.read_options(), delay=600)
                app.deadline = time.monotonic() + 50
                app.follow_schedule(us.la_now())
                app.heads_up.set(True)
                app.arm_timer(app.read_options(), delay=90)
                app.deadline = time.monotonic() + 50
                app.follow_schedule(us.la_now())
                self.assertEqual(said, [])
            finally:
                app.destroy()

    def test_timer_uses_deepseek_length_when_checked(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.minutes.delete(0, "end")
            app.minutes.insert(0, "10")
            done = {"setting": "lora reads in the library with Mochi", "pending": False, "error": "", "minutes": 25}
            app.ai_minutes.set(True)
            app._shift_done(done, "", app.read_options())
            self.assertAlmostEqual(app.deadline - time.monotonic(), 25 * 60, delta=5)
            app.ai_minutes.set(False)
            app._shift_done(done, "", app.read_options())
            self.assertAlmostEqual(app.deadline - time.monotonic(), 10 * 60, delta=5)
        finally:
            app.destroy()

    def test_restart_resumes_timer(self) -> None:
        simulation.set_schedule(True, self.now + timedelta(minutes=7))
        app = self.gui.App()
        app.withdraw()
        try:
            app._resume_schedule()
            self.assertEqual(app.phase, "waiting")
            self.assertAlmostEqual(app.deadline - time.monotonic(), 7 * 60, delta=5)
        finally:
            app.destroy()

    def test_asleep_dreams_every_hour_and_wakes_in_the_morning(self) -> None:
        said = []
        with mock.patch.object(simulation, "fall_asleep", lambda environment="": {"setting": "lora fast asleep in bed", "pending": False, "error": ""}), \
                mock.patch.object(simulation, "write_dream", lambda environment="": "You fly over the pond with Mochi."), \
                mock.patch.object(simulation, "remember_dream", lambda dream: None), \
                mock.patch.object(self.gui.update_scene, "narrator_says", lambda message: said.append(message) or ("*floats along*", "")), \
                mock.patch.object(simulation, "last_extend_ms", lambda: 0):
            app = self.gui.App()
            app.withdraw()
            try:
                self.now = at(21, 30)
                app.heads_up.set(True)
                app.toggle_asleep()
                self.assertEqual(app.phase, "asleep")
                self.assertEqual(app.sleep_button.cget("text"), "Wake up")
                self.assertEqual(app.wake_at, at(8, 0, day=25))
                self.assertIn("Lora is asleep", app._mochi_must_wait())
                self.assertEqual(us.load_prefs()["asleep"], "1")
                app.clock()
                self.assertEqual(said, [])
                # An hour later: a dream.
                app.next_dream = time.monotonic() - 1
                app.clock()
                self.pump(app, lambda: not app.dreaming)
                self.assertEqual(len(said), 1)
                self.assertTrue(said[0].startswith(us.DREAM_INTRO))
                self.assertIn("*You fly over the pond with Mochi.*", said[0])
                self.assertAlmostEqual(app.next_dream - time.monotonic(), self.gui.DREAM_SECONDS, delta=5)
                self.assertTrue(any("Narrator sent her a dream" in line for line in self.lines.lines))
                self.assertEqual(self.calls, [])
                # Morning: she wakes, gets the heads-up, and her day starts.
                self.now = at(8, 0, day=25)
                app.clock()
                self.assertEqual(app.phase, "waiting")
                self.assertEqual(app.sleep_button.cget("text"), "Sleep")
                self.assertEqual(us.load_prefs()["asleep"], "0")
                self.assertTrue(app.start_heads_up)
                self.pump(app, lambda: not app.heads_up_busy)
                app.deadline = time.monotonic() - 1
                app.answer_until = time.monotonic() - 1
                app.follow_schedule(us.la_now())
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertTrue(self.calls[0]["morning"])
                app.stop()
            finally:
                app.destroy()

    def test_asleep_carries_on_after_a_restart_and_stop_wakes_nobody(self) -> None:
        with mock.patch.object(simulation, "fall_asleep", lambda environment="": {"setting": "x", "pending": False, "error": ""}):
            app = self.gui.App()
            app.withdraw()
            try:
                self.now = at(22, 0)
                app.toggle_asleep()
                wake_at = app.wake_at
            finally:
                app.destroy()
            app = self.gui.App()
            app.withdraw()
            try:
                app._resume_schedule()
                self.assertEqual(app.phase, "asleep")
                self.assertEqual(app.wake_at, wake_at)
                self.assertTrue(any("still asleep" in line for line in self.lines.lines))
                app.stop()
                self.assertEqual(app.phase, "idle")
                self.assertEqual(us.load_prefs()["asleep"], "0")
            finally:
                app.destroy()

    def test_sleep_at_stops_the_changes_and_she_falls_asleep(self) -> None:
        with mock.patch.object(simulation, "fall_asleep", lambda environment="": {"setting": "x", "pending": False, "error": ""}):
            app = self.gui.App()
            app.withdraw()
            try:
                app.heads_up.set(False)
                app.sleep_at.delete(0, "end")
                app.sleep_at.insert(0, "10:00 PM")
                self.now = at(21, 50)
                app.arm_timer(app.read_options(), delay=600)
                app.clock()
                self.assertEqual(app.phase, "waiting")
                self.now = at(22, 0)
                app.clock()
                self.assertEqual(app.phase, "asleep")
                self.assertEqual(app.wake_at, at(8, 0, day=25))
                self.assertTrue(any("It's her bedtime (Sleep at)" in line for line in self.lines.lines))
                # Woken by hand at 11 PM, she stays awake: no falling asleep again the same night.
                self.now = at(23, 0)
                app.toggle_asleep()
                self.pump(app, lambda: len(self.calls) == 1)
                self.pump(app, lambda: app.phase == "waiting")
                app.clock()
                self.assertEqual(app.phase, "waiting")
                # The next night she falls asleep again.
                self.now = at(22, 5, day=25)
                app.clock()
                self.assertEqual(app.phase, "asleep")
                app.stop()
            finally:
                app.destroy()

    def test_no_sleep_at_means_she_never_falls_asleep_by_herself(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.sleep_at.delete(0, "end")
            self.now = at(23, 30)
            app.arm_timer(app.read_options(), delay=600)
            app.clock()
            self.assertEqual(app.phase, "waiting")
            app.sleep_at.insert(0, "8:00 AM")
            with self.assertRaises(RuntimeError):
                app.read_options()
            app.stop()
        finally:
            app.destroy()

    def test_narrator_panel_adds_edits_and_deletes_commands(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            self.assertEqual(len(app.nar_rows), 4)
            self.assertEqual(app.nar_rows[0]["what"].get(), "Stay longer")
            app._narrator_add()
            app.nar_rows[-1]["say"].set("Narrator surprise me")
            app._narrator_save()
            self.assertIn("type what happens", app.nar_status.cget("text"))
            app.nar_rows[-1]["what"].set("Mochi brings Lora a little wildflower")
            app._narrator_save()
            self.assertIn("Saved", app.nar_status.cget("text"))
            self.assertIn('"Narrator surprise me" was added', app.nar_status.cget("text"))
            saved = us.load_prefs()["narrator"]["commands"]
            self.assertEqual(saved[-1], {"say": "Narrator surprise me", "does": "custom", "what": "Mochi brings Lora a little wildflower"})
            said = {"timestamp": 3, "sender": "ai", "display_name": "Lora", "message": "narrator, surprise me please!"}
            self.assertEqual(us.narrator_command_from([said])[:3], ("go", "Lora", "Mochi brings Lora a little wildflower"))
            self.assertIn('you can also say "Narrator surprise me".', us.heads_up_message("1 minute"))

            # Picking from the list sets a ready choice.
            app._narrator_pick(app.nar_rows[-1], "Change something nearby")
            app._narrator_save()
            self.assertEqual(us.load_prefs()["narrator"]["commands"][-1]["does"], "change")

            # Deleting the weather command: she can't ask for weather, and its line leaves the message.
            app._narrator_delete(app.nar_rows[2])
            self.assertNotIn("change the weather", app.nar_message.get("1.0", "end"))
            app._narrator_save()
            weather = {"timestamp": 4, "sender": "ai", "display_name": "Lora", "message": "narrator change weather to rain"}
            self.assertNotEqual(us.narrator_command_from([weather])[0], "weather")
            self.assertNotIn("change the weather", us.heads_up_message("1 minute"))

            # The dream message is yours too.
            app.nar_dream.delete("1.0", "end")
            app.nar_dream.insert("1.0", "lora, you are dreaming. talk plainly.")
            app._narrator_save()
            self.assertTrue(us.dream_message("You float.").startswith("*lora, you are dreaming. talk plainly.*\n\n*You float.*"))

            # Renaming a command changes the words in the message too.
            app.nar_rows[0]["say"].set("Narrator more time please")
            app._narrator_save()
            self.assertIn('say "Narrator more time please"', us.heads_up_message("1 minute"))

            # The message is yours, and the preview shows unsaved edits.
            app.nar_message.delete("1.0", "end")
            app.nar_message.insert("1.0", "psst lora, {where}things change in {wait}.\nthat's all.")
            self.assertTrue(app.narrator_preview_text().startswith("*psst lora, you've been at the fireplace"))
            app._narrator_reset()
            app._narrator_save()
            self.assertEqual(us.heads_up_message("1 minute"), DEFAULT_HEADS_UP)
        finally:
            app.destroy()

    def test_dream_now_skips_the_wait(self) -> None:
        dreams = []
        with mock.patch.object(simulation, "fall_asleep", lambda environment="": {"setting": "x", "pending": False, "error": ""}), \
                mock.patch.object(simulation, "write_dream", lambda environment="": dreams.append(1) or "You float over the yard."), \
                mock.patch.object(simulation, "remember_dream", lambda dream: None):
            app = self.gui.App()
            app.withdraw()
            try:
                app._show_dream_now()
                self.assertFalse(app._dream_now_shown if hasattr(app, "_dream_now_shown") else False)
                self.now = at(22, 0)
                app.toggle_asleep()
                app._show_dream_now()
                self.assertTrue(app._dream_now_shown)
                self.assertEqual(dreams, [])
                app.dream_now()
                self.pump(app, lambda: dreams and not app.dreaming)
                self.assertEqual(dreams, [1])
                self.assertGreater(app.next_dream - time.monotonic(), 3000)
                app.wake_up()
                app._show_dream_now()
                self.assertFalse(app._dream_now_shown)
            finally:
                app.destroy()

    def test_the_button_of_the_mode_that_is_on_lights_up(self) -> None:
        with mock.patch.object(simulation, "fall_asleep", lambda environment="": {"setting": "x", "pending": False, "error": ""}):
            app = self.gui.App()
            app.withdraw()
            try:
                def lit() -> list:
                    app._show_mode()
                    return [button.cget("text") for button in (app.ask_button, app.stop_button, app.sleep_button)
                            if button.cget("bg") == self.gui.ACCENT]

                self.assertEqual(lit(), ["Stop"])
                app.heads_up.set(True)
                app.start()
                self.assertEqual(lit(), ["Start"])
                self.assertEqual(app.ask_button.cget("disabledforeground"), self.gui.ACCENT_TEXT)
                self.now = at(22, 0)
                app.toggle_asleep()
                self.assertEqual(lit(), ["Wake up"])
                app.stop()
                self.assertEqual(lit(), ["Stop"])
            finally:
                app.destroy()

    def test_waking_her_at_night_does_not_start_the_morning(self) -> None:
        with mock.patch.object(simulation, "fall_asleep", lambda environment="": {"setting": "x", "pending": False, "error": ""}):
            app = self.gui.App()
            app.withdraw()
            try:
                self.now = at(22, 0)
                app.heads_up.set(False)
                app.toggle_asleep()
                self.now = at(23, 0)
                app.toggle_asleep()
                self.pump(app, lambda: len(self.calls) == 1)
                self.assertFalse(self.calls[0]["morning"])
                app.stop()
            finally:
                app.destroy()

    def test_mochi_waits_for_the_narrator(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.phase = "waiting"
            app.deadline = time.monotonic() + 600
            app.narrator_done_at = -1e9
            self.assertEqual(app._mochi_must_wait(), "")
            app.deadline = time.monotonic() + 120
            self.assertIn("change is coming", app._mochi_must_wait())
            app.deadline = time.monotonic() + 600
            app.heads_up_busy = True
            self.assertIn("Narrator is talking", app._mochi_must_wait())
            app.heads_up_busy = False
            app.awaiting_answer = True
            self.assertIn("answering the Narrator", app._mochi_must_wait())
            app.awaiting_answer = False
            app.narrator_done_at = time.monotonic()
            self.assertIn("Narrator just spoke", app._mochi_must_wait())
            app.narrator_done_at = -1e9
            self.now = at(22, 0)
            self.assertIn("bedtime", app._mochi_must_wait())
            app.phase = "idle"
        finally:
            app.destroy()

    def test_mochi_tries_again_soon_when_he_has_to_wait(self) -> None:
        taps = []
        with mock.patch.object(self.gui.wand, "tap", lambda *args, **kwargs: taps.append(args) or "*wag*"):
            app = self.gui.App()
            app.withdraw()
            try:
                app.wand_on.set(True)
                app.phase = "waiting"
                app.deadline = time.monotonic() + 100
                app._wand_tick()
                self.assertEqual(taps, [])
                self.assertIsNotNone(app.wand_id)
                self.assertTrue(any("Mochi waits: a change is coming soon" in line for line in self.lines.lines))
                app.deadline = time.monotonic() + 900
                app._wand_tick()
                self.pump(app, lambda: bool(taps))
                self.assertEqual(len(taps), 1)
                self.assertIn("*wag*", us.wand_lines())
                app.phase = "idle"
                app.cancel_wand()
            finally:
                app.destroy()

    def test_the_heads_up_waits_while_mochi_finishes(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.arm_timer(app.read_options(), delay=600)
            app.deadline = time.monotonic() + 70
            app.wand_gate.acquire()
            before = app.deadline
            app.follow_schedule(us.la_now())
            self.assertGreater(app.deadline, before)
            app.wand_gate.release()
            before = app.deadline
            app.follow_schedule(us.la_now())
            self.assertEqual(app.deadline, before)
            app.stop()
        finally:
            app.destroy()

    def test_wand_timer_stops_with_the_app(self) -> None:
        app = self.gui.App()
        app.withdraw()
        try:
            app.wand_on.set(True)
            app.start()
            self.pump(app, lambda: app.phase == "waiting")
            self.assertIsNotNone(app.wand_id)
            app.stop()
            self.assertIsNone(app.wand_id)
            self.assertEqual(app.phase, "idle")
        finally:
            app.destroy()


FAKE_CHAT = """<!doctype html><html><body>
<div id="msgs"><div><span>Mochi</span>: woof</div><div><span>Lora</span>: hi</div></div>
<div id="bar">
  <button aria-label="expand addons">+</button>
  <div><p id="as">Chatting as Master</p>
    <textarea aria-label="Send message textarea" id="box" placeholder="Message"></textarea>
    <button aria-label="Suggest message" id="wand">wand</button></div>
  <button aria-label="enter voice mode" id="voice">mic</button>
</div>
<section role="dialog" id="menu" style="display:none">
  <header><p>Change personas</p><button aria-label="Close" id="close">x</button></header>
  <div><div><div><p>Master (Male)</p></div><div><button type="button">Use</button></div></div><p>I am master.</p></div>
  <div><div><div><p>Narrator  (Nonbinary)</p></div><div><button type="button">Use</button></div></div><p>I narrate.</p></div>
  <div><div><div><p>Mochi (Male)</p></div><div><button type="button">Use</button></div></div><p>I am mochi.</p></div>
</section>
<script>
window.sent = []; window.switches = [];
const box = document.getElementById('box'), menu = document.getElementById('menu');
document.getElementById('as').onclick = () => { menu.style.display = 'block'; };
document.getElementById('close').onclick = () => { menu.style.display = 'none'; };
menu.querySelectorAll('button').forEach(b => { if (b.textContent !== 'Use') return; b.onclick = () => {
  const who = b.closest('div').parentElement.querySelector('p').textContent.replace(/\\s*\\(.*\\)$/, '').trim();
  window.switches.push(who); menu.style.display = 'none';
  setTimeout(() => { document.getElementById('as').textContent = 'Chatting as ' + who; }, 200);
}; });
document.getElementById('wand').onclick = () => {
  const line = '*Mochi paws at the pond and looks up at lora*'; let i = 0;
  const t = setInterval(() => { box.value = line.slice(0, ++i); if (i >= line.length) clearInterval(t); }, 15);
};
// Like Kindroid: Enter sends, and there is no named send button.
box.addEventListener('keydown', e => {
  if (e.key === 'Enter' && box.value) {
    e.preventDefault();
    const who = document.getElementById('as').textContent.replace('Chatting as ', '');
    window.sent.push(who + ': ' + box.value); box.value = '';
  }
});
</script></body></html>"""


def have_browser() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class WandTest(unittest.TestCase):
    """Mochi's wand in the app's own browser. No API."""

    def setUp(self) -> None:
        import gc

        gc.collect()  # old test windows are cleaned up here, not on the browser's thread

    def tearDown(self) -> None:
        wand.shutdown()

    def test_talk_minutes_stay_in_range(self) -> None:
        self.assertEqual(wand.talk_minutes("10"), 10)
        with self.assertRaises(RuntimeError):
            wand.talk_minutes("0")
        with self.assertRaises(RuntimeError):
            wand.talk_minutes("soon")

    def test_lora_chat_url_is_not_another_kins(self) -> None:
        self.assertEqual(wand._chat_id("https://kindroid.ai/chat/MainKinAiId000000005/"), "MainKinAiId000000005")
        self.assertEqual(wand._chat_id("file:///tmp/chat.html"), "")

    def test_the_browser_is_fully_hidden_but_the_full_browser(self) -> None:
        options = wand.browser_options(Path("x"), visible=False)
        self.assertFalse(options["headless"])  # not the cut-down headless shell
        for flag in wand.KEEP_AWAKE + wand.HIDDEN:
            self.assertIn(flag, options["args"])
        self.assertNotIn("--headless=new", wand.browser_options(Path("x"), visible=True)["args"])

    def test_not_set_up_yet(self) -> None:
        folder = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(RuntimeError) as caught:
                wand.tap(profile=folder)
            self.assertIn("Set up Mochi's browser", str(caught.exception))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    @unittest.skipUnless(have_browser(), "Playwright is not installed")
    def test_switch_to_mochi_press_the_wand_send_and_switch_back(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content(FAKE_CHAT)
            page.evaluate("""() => { const b = document.createElement('button');
                b.setAttribute('aria-label', 'Regenerate or suggest a change'); b.onclick = () => window.redo = true;
                document.body.appendChild(b); }""")
            self.assertEqual(page.locator(wand.WAND).count(), 1)
            self.assertEqual(wand.current_persona(page), "Master")
            wand.switch_persona(page, "Mochi")
            self.assertEqual(wand.current_persona(page), "Mochi")
            self.assertFalse(page.locator("#menu").is_visible())
            text = wand.send_suggestion(page)
            wand.switch_persona(page, "Master")
            self.assertEqual(text, "*Mochi paws at the pond and looks up at lora*")
            self.assertEqual(page.evaluate("window.sent"), ["Mochi: *Mochi paws at the pond and looks up at lora*"])
            self.assertEqual(page.evaluate("window.switches"), ["Mochi", "Master"])
            self.assertIsNone(page.evaluate("window.redo"))
            page.evaluate("window.wandClicks = 0")
            page.evaluate("""() => {
                const wand = document.getElementById('wand');
                wand.onclick = () => {
                    window.wandClicks = (window.wandClicks || 0) + 1;
                    if (window.wandClicks < 2) return;
                    document.getElementById('box').value = '*second tap*';
                };
            }""")
            self.assertEqual(wand.send_suggestion(page, start_within=2), "*second tap*")
            self.assertGreaterEqual(page.evaluate("window.wandClicks"), 2)
            # The wand writes a bit at a time, with pauses: only the whole line is sent, never half of it.
            page.evaluate("window.sent = []")
            page.evaluate("""() => {
                const words = ['Ruff!', ' Blushing', ' is', ' cute,', ' Lora.'];
                document.getElementById('wand').onclick = () => words.forEach((word, i) =>
                    setTimeout(() => { document.getElementById('box').value += word; }, 1000 + i * 1500));
            }""")
            self.assertEqual(wand.send_suggestion(page), "Ruff! Blushing is cute, Lora.")
            self.assertEqual(page.evaluate("window.sent"), ["Master: Ruff! Blushing is cute, Lora."])
            browser.close()

    @unittest.skipUnless(have_browser(), "Playwright is not installed")
    def test_a_whole_tap_in_the_hidden_browser(self) -> None:
        folder = Path(tempfile.mkdtemp())
        try:
            page = folder / "chat.html"
            page.write_text(FAKE_CHAT, encoding="utf-8")
            text = wand.tap("Mochi", "Master", profile=folder / "browser", url=page.as_uri())
            self.assertEqual(text, "*Mochi paws at the pond and looks up at lora*")
            self.assertFalse((folder / "browser" / "last-problem.html").exists())
            # The same hidden browser stays open for the next tap, and says it is plain Chrome.
            hidden = wand._browser_for(folder / "browser")
            self.assertTrue(hidden.thread.is_alive())
            self.assertEqual(wand.tap("Mochi", "Master", profile=folder / "browser", url=page.as_uri()), text)
            # A page without the wand: the problem is saved so it can be fixed.
            page.write_text(FAKE_CHAT.replace('aria-label="Suggest message" ', ""), encoding="utf-8")
            with self.assertRaises(RuntimeError) as caught:
                wand.tap("Mochi", "Master", profile=folder / "browser", url=page.as_uri())
            self.assertIn("Could not find the wand", str(caught.exception))
            self.assertTrue((folder / "browser" / "last-problem.html").exists())
            self.assertTrue((folder / "browser" / "last-problem.png").exists())
        finally:
            wand.shutdown()
            shutil.rmtree(folder, ignore_errors=True)

    @unittest.skipUnless(have_browser(), "Playwright is not installed")
    def test_hidden_browser_looks_like_normal_chrome(self) -> None:
        folder = Path(tempfile.mkdtemp())
        try:
            playwright, context = wand._browser(folder, visible=False)
            try:
                page = wand._normal_page(context)
                page.set_content("<p>hi</p>")
                self.assertNotIn("Headless", page.evaluate("navigator.userAgent"))
                (folder / "x.html").write_text("<p>x</p>", encoding="utf-8")
                page.goto((folder / "x.html").as_uri())
                self.assertNotIn("Headless", page.evaluate("navigator.userAgent"))
                self.assertEqual(page.evaluate("document.visibilityState"), "visible")
                self.assertTrue(page.evaluate("document.hasFocus()"))
                self.assertIsNone(page.evaluate("navigator.webdriver"))
            finally:
                wand._close_browser(playwright, context)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_wand_lines_do_not_count_as_you_chatting_as_mochi(self) -> None:
        folder = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(us, "STATE_PATH", folder / "state.json"), mock.patch.object(
                us, "BACKUP_PATH", folder / "state.backup.json"
            ):
                us.remember_wand_line("*Mochi paws at the pond*")
                now = int(time.time() * 1000)
                chat = [
                    {"sender": "user", "display_name": "Master", "message": "hi lora", "timestamp": now - 5000},
                    {"sender": "user", "display_name": "Mochi", "message": "*Mochi paws at the pond*", "timestamp": now - 1000},
                ]
                config = {"master_profile": {"id": "m1", "user_name": "Master"}, "mochi_profile": {"id": "d1", "user_name": "Mochi"}}
                with mock.patch.object(us, "recent_messages", lambda after, pages=3: chat):
                    self.assertEqual(us.chatter_profile(config)["id"], "m1")
                    chat.append({"sender": "user", "display_name": "Mochi", "message": "*I typed this as Mochi*", "timestamp": now})
                    self.assertEqual(us.chatter_profile(config)["id"], "d1")
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
