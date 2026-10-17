# Recaper

A small Flask web app that cleans up a Microsoft Teams transcript copied as text, and translates it.

## Run it

Double-click `run.bat`, or from this folder:

```
python -m pip install -r requirements.txt
python app.py
```

The browser opens at http://127.0.0.1:5000. Stop the app with Ctrl+C in the console window.
Translation needs an internet connection; everything else works offline.

## What it does

1. **Upload** a `.txt` transcript by drag and drop or the browse button.
2. **Meeting type**
   - *Teams meeting*: each person has their own Teams ID. Lines from the same ID in a row become one block.
   - *Hall meeting*: several people share one microphone ID. The app looks for hand-overs
     ("I'll pass on to Georg", "Thanks, Dan", "Hi everyone", "That's a great question", a closing "Thank you.")
     and lists them as speaker changes for you to confirm.
   - *Mixed*: both. Tick which IDs are hall microphones (the one with the most words is ticked by default).
3. **Speaker changes** (hall and mixed only): tick or untick each suggestion, fix the name, move it one
   sentence earlier or later with ◀ ▶, or add your own by typing a few words from where the new person starts.
   Neighbouring blocks that end up with the same name are merged.
4. **Line breaks**
   - *Auto*: whole sentences packed into lines under 150 letters, broken at a full stop wherever possible
     (a single sentence longer than that is broken at a comma, then at a space).
   - *At each full stop*: one sentence per line.
   - *Every 150 letters*: broken at the last space before 150 letters.
5. **Output**: speaker name on one line, the content below, a blank line between speakers. Timestamps and the
   extra lines Teams adds (avatar initials, "47 minutes 13 seconds47:13") are removed. Tick
   "Show the start time" if you want it next to the name.
6. **Translate** (appears once the transcript is built): DeepL by default, Persian by default, right to left,
   with a switch for left to right. The transcript is sent 20 lines at a time and the results are joined back
   together in order, so long meetings are never too large for one request. Edits you make in the clean
   transcript box are included. The page also shows how much of the monthly DeepL quota is used.
   Google (free) is still there as a second choice.

## DeepL key

Put the key in `API.txt` in this folder, on a line like this:

```
DeepL API Key: your-key-here
```

The app reads it each time it translates, so a new key works without a restart. Keys ending in `:fx`
(the free plan) go to api-free.deepl.com, other keys to api.deepl.com. Keep `API.txt` private and do not
share it with the rest of the code.

## Files

- `app.py`: the Flask routes and translation (DeepL with the key from `API.txt`, or Google through deep-translator)
- `transcript.py`: parsing, hand-over detection, merging and line breaking
- `templates/index.html`, `static/app.js`, `static/style.css`: the page
