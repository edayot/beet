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

### namespace

`@function(strip_final_newline) namespace:test`

```mcfunction

```

### test

`@context_float_provider test:test1`

```json
{
  "type": "minecraft:mul",
  "inputs": [
    {
      "type": "minecraft:constant",
      "value": 2.0
    }
  ]
}
```
