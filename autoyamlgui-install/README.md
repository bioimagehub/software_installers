# autoyamlgui

A cross-platform (Linux, Windows, macOS) automation tool that reads a YAML config
file and presses buttons on screen in a given order. It can wait for buttons to
appear or disappear, type text, loop over steps, and enforce timeouts.

## Quick start

```bash
# Run a script
uv run autoyamlgui configs/start_menu_test.yaml

# Validate a config without executing
uv run autoyamlgui --dry-run configs/start_menu_test.yaml

# Capture mode — record clicks and build a config interactively
uv run autoyamlgui --capture
```

## Capture mode

Capture mode lets you build a YAML config by clicking on real buttons on your
screen. For each click:

1. A screenshot is taken of the full virtual desktop (all monitors).
2. A crop window opens showing the area around your click, zoomed 2×.
3. Draw a rectangle around the button you want to capture.
4. Enter a name, choose a command (`click`, `wait_appear`, `wait_disappear`,
  `click_double`, `click_and_type`), and optionally enter text + Enter.
5. The cropped image is saved to `<outdir>/buttons/<name>.png`.
6. A step is appended to the YAML config.

When you're done, the config is written to `<outdir>/config.yaml`.

If `<outdir>/config.yaml` already exists, capture mode asks whether to:
- continue working on the existing config (append new captured steps), or
- archive it as `config_legacy_N.yaml` and start a new config.

If you continue with an existing config, capture mode also asks whether to run
the existing steps before starting new recordings.

```bash
uv run autoyamlgui --capture
```

## YAML format

A config file has four top-level sections:

| Section | Required | Description |
|---|---|---|
| `name` | no | A label for the script, used in logging |
| `defaults` | no | Default values applied to every step (can be overridden per step) |
| `environment` | yes | Global settings such as the button image path |
| `steps` | yes | The ordered list of actions to perform |

### Minimal example

```yaml
name: "Ollama start sequence"
defaults:
  timeout: inf
  confidence: 0.8

environment:
  buttonpath: '/path/to/buttons'

steps:
  - button: 'start.png'
  - wait: 5s
  - button: 'ollama.png'
    command: wait_disappear
    timeout: 1m
```

---

## Top-level fields

### `name` (optional)
A human-readable label for the script. Shown in logs.

### `defaults` (optional)
Default values inherited by every step. Any step can override these.

| Key | Type | Default | Description |
|---|---|---|---|
| `timeout` | duration | `inf` | Max time to wait for a button before failing |
| `confidence` | float (0–1) | `0.8` | Match threshold for image recognition |

### `environment` (required)

| Key | Type | Description |
|---|---|---|
| `buttonpath` | string | Directory where button images are stored. Button filenames in steps are resolved relative to this path. |

---

## Steps

Each item in the `steps` list is one of the following step types.

### 1. Button step

Find an image on screen and perform an action on it.

```yaml
- button: 'start.png'
  command: click          # optional, defaults to: click
  timeout: 1m              # optional, overrides defaults.timeout
  confidence: 0.9          # optional, overrides defaults.confidence
```

#### `command` values

| Command | Description |
|---|---|
| `click` *(default)* | Find the button image on screen and click it |
| `click_double` | Find the button image on screen and double-click it |
| `wait_appear` | Wait until the button image appears on screen |
| `wait_disappear` | Wait until the button image is no longer on screen |
| `click_and_type` | Click the button, then type `text` into the focused field |

#### Extra keys for `click_and_type`

| Key | Type | Description |
|---|---|---|
| `text` | string | The text to type after clicking |
| `enter` | bool | If `true`, press Enter after typing (default: `false`) |

Example:

```yaml
- button: 'search.png'
  command: click_and_type
  text: 'ollama run glm-5.2:cloud'
  enter: true
```

### 2. Wait step

Pause execution for a fixed duration.

```yaml
- wait: 5s
```

### 3. Repeat step (loop)

Jump back to an earlier step and run a number of iterations.

```yaml
- repeat:
    from: 2          # 1-based step index to jump back to
    times: 3         # number of iterations
    delay: 1s        # optional: pause between iterations
```

> **Note:** `from` is a 1-based index into the `steps` list. Be careful when
> inserting steps above a `repeat` — the index will not auto-update.

---

## Duration format

Durations are written as a number followed by a unit (no space):

| Suffix | Meaning |
|---|---|
| `ms` | milliseconds |
| `s` | seconds |
| `m` | minutes |
| `h` | hours |
| `d` | days |
| `inf` | no limit (default for `timeout`) |

Examples: `500ms`, `5s`, `2m`, `1h`, `inf`.

---

## Full example

```yaml
name: "Ollama start sequence"
defaults:
  timeout: inf
  confidence: 0.8

environment:
  buttonpath: '/path/to/buttons'

steps:
  - button: 'start.png'            # click start.png
  - wait: 5s                       # wait 5 seconds
  - button: 'ollama.png'
    command: wait_disappear        # wait for ollama.png to disappear
    timeout: 1m
  - button: 'start.png'
    timeout: 1m
  - button: 'search.png'
    command: click_and_type        # click search.png, type text, press Enter
    text: 'ollama run glm-5.2:cloud'
    enter: true
  - repeat:
      from: 2                      # jump back to step 2 (the wait)
      times: 3
      delay: 1s
```

## Running

```bash
uv run autoyamlgui <path/to/config.yaml>
```