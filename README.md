# AllAI

AllAI reviews several Anki vocabulary cards inside one short, generated sentence
to improve retention through varied context. In a study of 26 Danish learners,
[Paddags et al. (2024)](https://aclanthology.org/2024.bea-1.29/) reported that
this method taught new words four times faster than conventional spaced repetition.

AllAI does **not** replace Anki or FSRS. It reads Anki's queue and sends every
rating back through Anki's normal scheduler.

## Workflow

```text
Due/new LangCards in a selected deck
                  |
                  v
Anki chooses cards in scheduler order
                  |
                  v
AllAI groups up to 4 words by default (at most 2 new)
                  |
                  v
LLM writes one short sentence containing those words
                  |
                  v
Reveal and rate every word: Again / Hard / Good / Easy
                  |
                  v
AllAI calls Anki's answer_card() once per card, sequentially
                  |
                  v
FSRS schedules the cards; AllAI queries the next batch
```

This means:

- AllAI and normal review use the same cards, history, due dates, and limits.
- Reviewed cards count as reviewed in Anki. There is no separate AllAI schedule.
- Exiting before a round is submitted leaves that round untouched.
- Cards that AllAI cannot use remain available for normal review.

## LangCard

AllAI needs a predictable note structure so it can identify the tested word,
meaning, reading, and example without guessing which arbitrary field is which.

| Field | Contents |
|---|---|
| `Target` | Word or phrase being learned |
| `Native` | Meaning in the learner's native language |
| `Example` | Optional example sentence; HTML such as `<br>` is allowed |
| `Reading` | Optional pronunciation or pinyin |
| `Extra` | Optional links, images, notes, or other untouched HTML |

Each LangCard note creates two normal Anki cards:

```text
Recognition: Target -> Native
Production:  Native -> Target
```

AllAI uses the same directions:

- **Recognition** generates a sentence in the target language.
- **Production** generates a sentence from the native-language meanings.
- **Both** alternates between available recognition and production cards.

Other note types are ignored and continue through normal Anki review.

## Getting cards in

### Existing notes

Open **Tools > AllAI > Migrate notes**.

```text
Choose source deck + note type
              |
              v
AllAI parses Front/Back or `target | native <br> example`
              |
              v
Create LangCard copies, optionally suspend originals
```

Migration is a one-time bridge for existing cards. Use the no-suspension mode
first if you want to inspect the results safely.

### New notes

Import them directly as `LangCard` and map CSV columns to the fields above. No
migration is needed afterward. Spaces around a CSV separator are harmless when
the imported field values are trimmed.

## Daily use

1. Open **Tools > AllAI > Start session**.
2. Select a deck (or all configured decks) and a CEFR sentence level.
3. Reveal meanings when useful, then rate every word.
4. Press **Next** to commit the complete round.

Keyboard controls:

| Key | Action |
|---|---|
| `Up` / `Down` | Move between words |
| `Space` | Toggle reveal |
| `1` / `2` / `3` / `4` | Again / Hard / Good / Easy |
| `R` | Replay sentence audio when available |

## Configuration

Open **Tools > AllAI > Settings** and set:

- An OpenAI-compatible base URL, API key, and model.
- Optional local sentence audio and a Piper voice for each language deck.
- Piper voice speed and local runtime installation.

Deck and CEFR level are selected when starting a session. Anki's add-on config
controls `card_mode` (`recognition`, `production`, or `both`), `due_only`,
`include_new_cards`, `words_per_sentence`, and `max_new_words_per_round`.

## Local audio

AllAI maps each language deck to a locally installed Piper voice. Voice models
are downloaded from **Settings > Deck voices**; they are not bundled with the
repository. Production rounds do not generate audio.

Setup:

1. Install Anki's playback backend. On Arch Linux or CachyOS, run
   `sudo pacman -S --needed mpv`, then restart Anki.
2. Open **Tools > AllAI > Settings** and enable local sentence audio.
3. In **Deck voices**, assign a voice to each deck that should have audio.
4. Click **Install / Repair**. AllAI creates an isolated Piper runtime and
   downloads every assigned voice that is missing. Internet access is required
   for this step only.
5. Select a configured deck row, click **Play test**, then save with **OK**.

Do not install Piper or copy models manually. AllAI manages this directory:

```text
user_files/piper/current/
├── runtime/                  # isolated Python environment with Piper
├── models/
│   ├── <voice>.onnx         # downloaded voice model
│   └── <voice>.onnx.json    # voice configuration
└── manifest.json
```

`user_files/` is ignored by Git. Voice models therefore remain local and must be
installed separately on another computer.

```text
Round appears -> Piper creates WAV in background -> Play audio / R -> temp WAV removed on exit
```

**Install / Repair** verifies assigned models and repairs missing or damaged
files. **Remove selected voice** removes only the model assigned to the selected
deck; the shared Piper runtime and other voices remain available.

## Development

```bash
python3 -m unittest discover -s tests -v
```
