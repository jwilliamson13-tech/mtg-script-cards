# mtg-script-cards

You give it the transcript of a Magic: The Gathering video, and it downloads an image of every card the host mentions, numbered in the order they come up. It knows that "Tropical Island" is one card and not the basic Island, and it holds back anything it isn't sure of so you can decide.

## Install

You need Python 3.10 or newer. Get it from [python.org](https://www.python.org/downloads/). On Windows, tick "Add python.exe to PATH" in the installer.

Then get the tool. Either download it from GitHub (the green "Code" button, then "Download ZIP", then unzip it), or, if you have git:

```
git clone https://github.com/jwilliamson13-tech/mtg-script-cards.git
```

Open a terminal (PowerShell on Windows, Terminal on macOS), go into the tool's folder and install the two libraries it needs. The folder is called `mtg-script-cards-main` if you downloaded the ZIP.

Windows:

```
cd mtg-script-cards
python -m pip install -r requirements.txt
```

macOS:

```
cd mtg-script-cards
python3 -m pip install -r requirements.txt
```

On macOS, type `python3` wherever this README says `python`. If pip refuses with "externally-managed-environment" (Python from Homebrew does this), make a private environment for the tool first, then install again. Run the `source` line again each time you open a new terminal:

```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## First run

Export a transcript from your editing software or from YouTube and run:

```
python mtgcards.py transcript.srt
```

If the transcript is not in the tool's folder, type its full path in quotes instead, or drag the file onto the terminal window to paste the path. The tool reads the transcript, finds the cards, and downloads their images from [Scryfall](https://scryfall.com). The images go in a new folder next to the transcript, named after it: `transcript_cards`. A one-line summary at the end tells you how many cards it found and where the folder is.

Inside the folder:

- One PNG per card, numbered by when the card is first mentioned: `001-thoughtseize.png`, `002-bitterblossom.png` and so on. A card mentioned ten times still gets one image.
- Double-faced cards get two images with the same number: `004-delver-of-secrets-insectile-aberration-front.png` and `...-back.png`.
- `manifest.csv`, which opens in Excel. It has one row per mention, in the order they come up, with the columns `order` (the number in the file name), `card`, `timestamp`, `matched_text` (the words in the transcript that matched), `confidence` (how it matched: `exact`, `decklist`, `nickname` or `fuzzy`) and `scryfall_url` (the card's page on Scryfall, where you can pick a different printing).

Mentions held for review (next section) are in `manifest.csv` too, with an empty `order`, because they have no image. The images are Scryfall's default printing, with transparent rounded corners. Running the same command again skips images that are already there.

## The review step

Some matches are guesses. When a card's name is also an everyday word ("Hope", "Opt", "Fog"), "I hope this works" would otherwise download Hope. When the transcript only sounds like a card ("factor fiction" for Fact or Fiction), the tool could be wrong. These are "held for review": listed after the run and not downloaded.

The list shows each card once, the words that matched, the timestamp, and the text around it. Fuzzy guesses also show a similarity score out of 100. Below the list is a ready-made command, your original command plus an `--include` for every held card:

```
python mtgcards.py transcript.srt --include "Fact or Fiction" --include "Hope"
```

Delete the `--include "..."` for each card you don't want, then run what's left. The accepted cards download and take their place in the numbering. Images already in the folder are renamed to their new numbers, not downloaded again. On macOS, change the `python` at the start of the printed command to `python3`.

On a long video the list can run to a hundred cards, nearly all of them noise ("Well", "Take", "Six"). It is then quicker to type your original command again and add an `--include` for the few you want.

`--include` works for any card, not only the ones on the list. You can add one the tool missed entirely, and the name doesn't have to be exact. A misspelling like `--include "thoughtsize"` is looked up on Scryfall, and the tool prints which card it took. Several names can go in one `--include`, separated by commas. Cards the built-in card list leaves out on purpose, like those from joke sets such as Unstable, are only reachable this way.

## Decklists

If the video is about known decks, give the tool the decklists. It becomes much better at catching those cards, even badly misheard ones:

```
python mtgcards.py transcript.srt --decklist faeries.txt --decklist devotion.txt
```

`--decklist` takes a plain text file in the format deck sites export, one card per line with the quantity first. Section headers like `Sideboard`, blank lines, lines starting with `//` or `#`, `4x` quantities and a set code after the name like `(LRW) 145` are all fine:

```
4 Mistbind Clique
4 Spellstutter Sprite
2 Thoughtseize (LRW) 145

Sideboard
2 Flashfreeze
```

A name the tool doesn't recognize gets a warning and is skipped.

What a decklist changes:

- Decklist cards download without review whenever they are matched, including sideboard cards.
- A decklist adds cards and never filters. A card that is not in any decklist still downloads if it is mentioned.
- One distinctive word from a decklist card is enough: with Snapcaster Mage in the list, "snapcaster" alone finds it. Ordinary English words don't count, and neither do words found in many card names ("mage", "faerie", "jace"). When two cards in your decklists share a word, that word alone matches neither of them.

## Nicknames

Hosts say "bolt", "goyf" and "trop". The file `data/nicknames.json` maps these to the real card names, and nickname matches download without review. Open it in any text editor (Notepad is fine) and add your channel's:

```
{
  "trop": "Tropical Island",
  "bolt": "Lightning Bolt",
  "mind sculptor": "Jace, the Mind Sculptor"
}
```

The rules:

- The nickname goes on the left and the card's name on the right, each in double quotes, with a comma after every line but the last. No comments.
- Capitals and punctuation in the nickname don't matter, and a nickname can be several words.
- The card name has to be spelled exactly as on Scryfall (capitals don't matter). A misspelled one gets a warning when you run the tool, and that line is skipped.
- Every nickname match downloads without review, so don't add a word the host also says for other reasons. "library" is not in the file because of "search your library".

To use a different file, run with `--nicknames my_nicknames.json`. That file replaces the bundled one, so copy across any entries you still want.

## Transcripts and timestamps

The tool reads plain text (`.txt`), SubRip (`.srt`) and WebVTT (`.vtt`). It works out which one it has from the content, so a wrong file extension doesn't matter. YouTube's auto-generated captions, where each line repeats in the next caption, are handled.

For SRT and VTT, a mention's timestamp in `manifest.csv` is the start of the caption where the card's name begins, as hours, minutes, seconds and milliseconds (`00:00:35.760`). Plain text has no times, so that column is empty.

## Transcribing a video with `--audio` (optional)

If you have the video but no transcript, the tool can make one. This needs an extra install of about 250 MB:

```
python -m pip install -r requirements-whisper.txt
```

Then:

```
python mtgcards.py --audio episode.mp4 --decklist faeries.txt
```

The first time you use a model it downloads to `%USERPROFILE%\.cache\huggingface\hub` on Windows or `~/.cache/huggingface/hub` on macOS. The default `small` model is about 0.5 GB. `--model large-v3` is about 3 GB and more accurate.

It is slow on a normal computer's processor. With the default model, an 81-minute episode took 21 minutes on a desktop CPU. An NVIDIA graphics card makes `large-v3` practical: the same episode took 13 minutes on a GTX 1080 Ti. To use one, also install:

```
python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

On Windows the tool finds these libraries itself, so there is nothing to add to PATH. It uses the graphics card when one works and the processor otherwise. `--device cpu` or `--device cuda` forces one.

The images go in `episode_cards` next to the video, as with a transcript. The transcript is saved as `episode.srt` next to that folder. A later run with the same video uses that file and skips transcription, so you can rerun with `--include` straight away. To transcribe again, for example with a different `--model`, delete the `.srt` first.

With `--decklist`, the decklist's card names are passed to the transcriber as spelling hints. In practice they help only in the first minute or two. The decklist matching described above does the real work.

## Choosing the output folder

```
python mtgcards.py transcript.srt --out "D:\Edit\Episode 12 cards"
```

The folder is created if it doesn't exist. With `--audio`, the `.srt` goes in the folder that contains the output folder.

## Updating the card list

The tool ships with a list of every card, so the first run only downloads images. When a new set comes out, update it:

```
python mtgcards.py --refresh
```

This downloads Scryfall's card file (about 25 MB), asks Scryfall which cards were never printed on paper so it can leave them out, and rewrites `data/cards.json`.

## Limitations

- Card names made only of common words, like "The End" or "Game Plan", download whenever the words are said. So does "Jund", which is also the name of a Planechase card. Delete those images by hand.
- "counter spell" puts Counterspell on the review list every time, even when the host means countering spells in general.
- Some mishearings are too far from the real name to catch. If a card is missing, give a decklist or add it with `--include`.
- `--include` does not read the nickname file, so `--include trop` is reported as not found. Use the full name.
- If a card drops out on a rerun (for example after you remove a decklist), its old image stays in the folder.
- No YouTube links. Download the captions yourself first.

## Credits and license

The tool is released under the MIT license (see `LICENSE`).

Card data and images come from [Scryfall](https://scryfall.com), which provides them free of charge for community projects. Scryfall's API documentation and data guidelines are at [scryfall.com/docs/api](https://scryfall.com/docs/api). The tool identifies itself to Scryfall as `mtg-script-cards/0.1.0` and spaces its requests as Scryfall asks. `data/cards.json` is built from Scryfall data.

This is an unofficial tool. It is not produced or endorsed by Scryfall or Wizards of the Coast. Magic: The Gathering, card names and card images are copyright Wizards of the Coast, LLC.

The common-word list in `data/common_words.txt` comes from [FrequencyWords](https://github.com/hermitdave/FrequencyWords) by Hermit Dave and is licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), not MIT.
