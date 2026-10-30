

compute default float {
    type: "max",
    inputs_: [1.2, 2.3, 3.4]
}

execute if predicate {invalid: true} run say "This should not be printed"