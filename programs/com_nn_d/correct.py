def COM_NN_D(a, b):
    # requires a >= 0 and b >= 0
    # ensures (a == b and result == 0) or (a < b and result == 1) or (a > b and result == 2)

    x = a
    y = b

    while x // 10 != y // 10:
        # invariant x >= 0 and y >= 0 and ((a == b and x == y) or (a < b and x < y) or (a > b and x > y))
        x = x // 10
        y = y // 10

    if x > y:
        return 2
    elif x < y:
        return 1
    else:
        return 0
