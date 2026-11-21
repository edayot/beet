data merge storage my_pack:data {players: [{coins: -5}]}

data merge storage my_pack:data {players: [{coins: 1.5f}]}

data merge storage my_pack:data {players: [{name: "Alex"}]}

data merge storage my_pack:data {players: [{coins: 3, vip: "yes"}]}

data merge storage my_pack:data {round: 11}

data merge storage my_pack:data {players: [{coins: 0, inventory: [{id: "minecraft:stone", count: "three"}]}]}

function my_pack:give_coins {amount: "lots"}

function my_pack:give_coins {reason: "No amount"}

summon zombie ~ ~ ~ {CanBreakDoors: "yes"}

summon zombie ~ ~ ~ {Health: "full"}

kill @e[type=zombie,nbt={CanBreakDoors: 1}]

setblock ~ ~ ~ chest{Items: 3}

give @s diamond_sword[damage="a lot"]

compute default float {type: "maxx", inputs: [1.2]}

data modify storage my_pack:data players[0].coins set value -1

data modify storage my_pack:data players append value {name: "No coins"}

data modify storage my_pack:data players[0] merge value {vip: 2b}

data modify storage my_pack:data round set value "first"

data modify storage my_pack:data round[0] set value 1

data modify entity @e[type=zombie,limit=1] CanBreakDoors set value "yes"
