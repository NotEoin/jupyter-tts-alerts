# jupyter-tts-alerts

Makes a Jupyter notebook tell you out loud when a cell finishes, how long it took, and whether it
blew up.

Useful when you're training something for twenty minutes and would rather not sit watching a
progress bar, or keep tabbing back to check whether it fell over in the first thirty seconds.

## Install

Needs a TTS engine on the system. It picks the first one it finds:

```bash
sudo apt install espeak-ng      # or espeak, or speech-dispatcher for spd-say
```

Then drop `notebook_tts.py` next to your notebook and run it in a cell:

```python
%run notebook_tts.py
```

That registers IPython pre/post-run hooks and an exception handler for the rest of the session.

## Behaviour

- Cells that finish in under 5 seconds say nothing, so you aren't narrated at while writing code.
- Cells that take longer announce the cell number and runtime.
- Errors are always announced regardless of runtime, in a different voice, so a failure at minute
  two doesn't sound like a success.

## Settings

At the top of the file:

| Name | Default | Meaning |
|---|---|---|
| `ANNOUNCEMENTS_ENABLED` | `True` | master switch |
| `TTS_VOLUME` | `50` | 0–200 |
| `MIN_RUNTIME_ANNOUNCE` | `5.0` | seconds before a successful cell is announced |

## Per-cell tags

Put either of these anywhere in a cell's source:

```python
# noannounce          -> stay quiet for this cell
# notebook-complete   -> announce total notebook runtime after this cell
```

`# notebook-complete` on your last cell is the useful one — you get told the whole run is done and
how long it took.

## Notes

Voices are picked automatically from `espeak --voices=en`, one male and one female, so success and
failure sound different. On `spd-say` it falls back to the default voice and both sound the same.

Speech runs in a subprocess and a new announcement cancels the one in progress, so a burst of quick
cells doesn't queue up a backlog it then has to read through.

## Licence

MIT — see [LICENSE](LICENSE).
