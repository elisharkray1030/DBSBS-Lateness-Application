from records import BoarderRecord


def record(name, bed="101", frequency=0, total_minutes=0, total_points=0):
    return BoarderRecord(
        name=name,
        bed=bed,
        frequency=frequency,
        total_minutes=total_minutes,
        total_points=total_points,
    )
