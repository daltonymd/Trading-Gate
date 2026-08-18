import os
import databento as db

count = 0

def on_record(record):
    global count

    if isinstance(record, db.MBP1Msg):
        count += 1

        if count <= 20:
            print(
                "MNQ LIVE",
                "instrument_id=", record.hd.instrument_id,
                "price=", record.price,
                "size=", record.size,
                "action=", record.action,
                "side=", record.side,
                "ts=", record.ts_recv,
            )

def on_error(exc):
    print("DATABENTO ERROR:", repr(exc))

client = db.Live(
    key=os.environ["DATABENTO_API_KEY"],
    reconnect_policy="reconnect",
)

client.subscribe(
    dataset="GLBX.MDP3",
    schema="mbp-1",
    symbols="MNQ.FUT",
    stype_in="parent",
)

client.add_callback(
    record_callback=on_record,
    exception_callback=on_error,
)

print("Connecting to Databento MNQ live feed...")

client.start()
client.block_for_close(timeout=15)

print()
print("LIVE TEST COMPLETE")
print("MBP-1 RECORDS RECEIVED:", count)
