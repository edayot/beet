

compute default float {
    type: "max",
    inputs: [1.2, 2.3, 3.4]
}

compute default float {
    type: "min",
    inputs: [1.2, 2.3, {type: "minecraft:constant", value: 3.4}]
}
