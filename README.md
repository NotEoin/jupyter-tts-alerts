# 🔊 jupyter-tts-alerts

![licence](https://img.shields.io/badge/licence-MIT-blue?style=flat-square)
![platform](https://img.shields.io/badge/platform-Linux-lightgrey?style=flat-square)
![python](https://img.shields.io/badge/python-3.8+-blue?style=flat-square)

> *Go and make a coffee. Your notebook will shout if it needs you.*

Your Jupyter notebook announces out loud when a long cell finishes, how long it took, and whether it
crashed — so you don't have to sit watching a progress bar for six hours.

![Which cells are announced, and what is said](./media/behaviour.png)

## 🌟 Highlights

- **Leave the room.** Long runs tell you when they're done from anywhere you can hear.
- **Failures sound different.** Errors are announced in a different voice, so you know something
  broke without listening to the words.
- **Quiet while you work.** Anything under five seconds says nothing, so you're not narrated at
  while writing code.
- **Silence any cell** with a `# noannounce` comment.
- **One line to set up.** No configuration, no notebook changes.
- **Tiny.** A single 150-line file and no Python dependencies.

## ℹ️ Overview

Written as a side-project during my dissertation, where I was running lengthy notebooks that took six or more hours a pass. Sitting at a desk
watching a spinner is a poor use of an afternoon, but so is coming back after an hour to find it
died on cell 9 forty minutes ago.

It hooks IPython's cell events, so nothing in your notebook has to change.

### ✍️ Author

Built by [@NotEoin](https://github.com/NotEoin) 

## 🚀 Usage

Run it once in your notebook and forget about it:

```python
%run notebook_tts.py
```

That's the whole setup. From then on:

- Cells under 5 seconds → silent
- Longer cells → *"Cell 12. 6 minutes 20 seconds."*
- Errors → announced immediately, in the other voice: *"Error in cell 12."*

### Per-cell tags

Put either of these anywhere in a cell:

```python
# noannounce           # stay quiet for this cell
# notebook-complete    # announce total runtime after this cell
```

`# notebook-complete` on your last cell is the useful one — you get told the whole run is finished
and how long it took.

### Settings

At the top of the file:

| Setting | Default | What it does |
|---|---|---|
| `ANNOUNCEMENTS_ENABLED` | `True` | Master switch |
| `TTS_VOLUME` | `50` | Volume, 0–200 |
| `MIN_RUNTIME_ANNOUNCE` | `5.0` | Seconds before a successful cell is announced |

## ⬇️ Installation

Install a speech engine — it uses the first one it finds:

```bash
sudo apt install espeak-ng      # or espeak, or speech-dispatcher for spd-say
```

Then drop the file next to your notebook:

```bash
git clone https://github.com/NotEoin/jupyter-tts-alerts.git
cp jupyter-tts-alerts/notebook_tts.py .
```

Requires Python 3.8+ and Jupyter. No Python packages needed.

## 🧭 Limitations

- **Not packaged yet.** `%run notebook_tts.py` with the file alongside your notebook is clumsy —
  proper `pip install` support is the main thing outstanding.
- **Linux only.** Engine detection looks for `espeak-ng`, `espeak` and `spd-say`. macOS `say` and
  Windows SAPI would each be a small addition.
- Two distinct voices need `espeak`; on `spd-say` success and failure sound the same.
- **Errors are announced twice.** `set_custom_exc` and `post_run_cell` both fire on a failing cell,
  and each increments the counter — so a crash says "Error in cell 4" and then "Error in cell 5".
  Cosmetic, but it also means the cell numbers drift after the first error.

## 💭 Feedback and contributing

macOS and Windows support would be genuinely useful and neither should be hard — pull requests very
welcome. Otherwise open an [issue](https://github.com/NotEoin/jupyter-tts-alerts/issues).

## 📄 Licence

MIT — see [LICENSE](LICENSE).
