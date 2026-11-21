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

`@context_float_provider namespace:test`

```json
{
  "type": "min",
  "inputs": [
    1.2,
    2.3,
    {
      "type": "minecraft:constant",
      "value": 3.4
    }
  ]
}
```

`@function namespace:test`

```mcfunction
compute default float {type: "max", inputs: [1.2d, 2.3d, 3.4d]}
compute default float {type: "min", inputs: [1.2d, 2.3d, {type: "minecraft:constant", value: 3.4d}]}
compute default integer {type: "max", inputs: [1, 2, 3]}
```
