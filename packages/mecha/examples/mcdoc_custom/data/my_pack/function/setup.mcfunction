data merge storage my_pack:data {round: 1, players: [{coins: 10, name: "Steve", vip: true}]}
data merge storage my_pack:data {players: [{coins: 0, inventory: [{id: "minecraft:stone", count: 3}]}]}
function my_pack:give_coins {amount: 5}
function my_pack:give_coins {amount: 5, reason: "Won a round"}
summon zombie ~ ~ ~ {CanBreakDoors: 1b}
data modify storage my_pack:data players[0].coins set value 20
data modify storage my_pack:data players append value {coins: 1}
data modify storage my_pack:data players insert 0 value {coins: 2, vip: false}
data modify storage my_pack:data players[0] merge value {vip: true}
data modify storage my_pack:data players[{name: "Steve"}].name set value "Alex"
data modify storage my_pack:data players[].inventory append value {id: "minecraft:apple", count: 1}
data modify entity @e[type=zombie,limit=1] CanBreakDoors set value 1b
