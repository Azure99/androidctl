# Observe, Action, Wait

Use this reference for screen-driven automation, ref freshness, app opening,
wait predicates, and `--screen-id` decisions.

## Core Loop

Run a screen loop that always refreshes after mutations:

```bash
androidctl observe
androidctl tap n1
androidctl wait --until idle
androidctl observe
```

Use refs from the latest `observe` only. After navigation, app switches, global
actions, `SCREEN_UNAVAILABLE`, `POST_ACTION_OBSERVATION_LOST`, or any uncertain
post-action observation, run `observe` before using refs again.

## Apps And URLs

Discover launcher apps, then open by `packageName`:

```bash
androidctl list-apps
androidctl open app:<package-name>
```

`list-apps` covers launcher-startable apps, not every installed package.

URL targets:

```bash
androidctl open https://example.com
androidctl open url:<target>
```

Bare targets must be absolute `http://` or `https://` URLs. Use the `url:` prefix
for other URL-like targets accepted by the device path.

## Actions

Ref actions require a current screen basis:

```bash
androidctl tap n3
androidctl long-tap n3
androidctl focus n4
androidctl type n4 "hello"
androidctl submit n4
androidctl scroll n8 down
```

`scroll` direction must be `up`, `down`, `left`, `right`, or `backward`.

Global actions can be sent without selecting an element:

```bash
androidctl back
androidctl home
androidctl recents
androidctl notifications
```

Global actions may briefly leave the current screen unknown. In that state, run
`observe` before any ref action or relative wait that depends on the current
screen.

## Wait Predicates

```bash
androidctl wait --until idle
androidctl wait --until screen-change
androidctl wait --until gone --ref n3
androidctl wait --until text-present --text "Done"
androidctl wait --until app --app com.android.settings
androidctl wait --until idle --timeout 5s
```

Predicate options:

- `idle`: no extra selector and no `--screen-id` requirement.
- `screen-change`: compares against an observed screen.
- `gone`: requires `--ref` and compares against an observed screen.
- `text-present`: requires `--text`.
- `app`: requires `--app`.

`--timeout` defaults to `2000ms`. Durations must be an integer followed by `ms`
or `s`.

## Screen Id Rules

For ref actions and relative waits, omit `--screen-id` in normal live operation.
The CLI uses the current live screen.

Use explicit `--screen-id` only for replay against a known observed screen:

```bash
androidctl tap n3 --screen-id screen-00013
androidctl wait --until screen-change --screen-id screen-00013
androidctl wait --until gone --ref n7 --screen-id screen-00013
```

Blank or whitespace-only `--screen-id` is a usage error. If the CLI reports
`SCREEN_UNAVAILABLE`, run `observe` to establish fresh screen truth. If it
reports `DEVICE_NOT_CONNECTED`, reconnect.
