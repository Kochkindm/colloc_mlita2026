def POZ_Z_D(n):
    # requires True
    # ensures (n == 0 and result == 0) or (n < 0 and result == 1) or (n > 0 and result == 2)

    if n == 0:
        return 0
    elif n < 0:
        return 1
    else:
        return 2
