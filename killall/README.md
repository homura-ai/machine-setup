# killall: moved to agents-killall

The emergency stop for AI agents (`agents-killall`, dry run by default, and `ka`,
the real stop) now has one general copy: the standalone **agents-killall** repository. Its
targets come from `~/.config/agents-killall/config.toml`; see that repo's
README and `config.example.toml`.

## On a new machine

`setup-new-machine.sh` does not install it. On the new machine, after the switch:

```sh
# Copy the standalone agents-killall repository to ~/code/agents-killall.
mkdir -p ~/.config/agents-killall
cp ~/code/agents-killall/config.example.toml ~/.config/agents-killall/config.toml
# edit the config for this machine, then:
~/code/agents-killall/install.sh    # refuses without a valid config
agents-killall                            # read the dry run before trusting ka
```

## The old config, in the new form

| old `config.json` key | `config.toml` |
| --- | --- |
| `watchdog_units` | `first_units` |
| `wake_unit_prefixes` | a group's `units`, as `prefix*` globs |
| `wake_commands` | that group's `names` |
| `extra_app_units` | the app group's `units` |
| `recovery_name`, `ka_open_gate` | `[ka_open]` `recovery_name`, `gate`, with `keep = ["app"]` for `ka open` |

The old copy also did four T3-specific things that agents-killall does not:
reroute claude-usage's T3 notifications (`usage-no-wake`), disable T3 Code's
SQLite schedules, stop Modal cloud apps (`--cloud`), and run an independent
Claude check after the stop.
