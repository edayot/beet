# Lectern snapshot

## Data pack

`@data_pack pack.mcmeta`

```json
{
  "pack": {
    "min_format": [
      121,
      0
    ],
    "max_format": [
      121,
      0
    ],
    "description": ""
  }
}
```

### my_pack

`@function my_pack:give_coins`

```mcfunction
$tellraw @a "$(amount) coins!"
```

`@function my_pack:setup`

```mcfunction
data merge storage my_pack:data {round: 1, players: [{coins: 10, name: "Steve", vip: true}]}
data merge storage my_pack:data {players: [{coins: 0, inventory: [{id: "minecraft:stone", count: 3}]}]}
function my_pack:give_coins {amount: 5}
function my_pack:give_coins {amount: 5, reason: "Won a round"}
summon zombie ~ ~ ~ {CanBreakDoors: 1b}
```
