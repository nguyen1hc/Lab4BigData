import math


def classify(value):
    if value > 0:
        label = "positive"
    else:
        label = "non-positive"
    print(label)
    return label


def accumulate(values):
    total = 0
    for value in values:
        if value < 0:
            continue
        total = total + value
    classify(total)
    return math.floor(total)

