#!/bin/bash
# Create extra InfluxDB buckets after initial setup
influx bucket create --name keyboard_bucket --org cognitive_lab --token my-super-secret-token --retention 0
influx bucket create --name mouse_bucket    --org cognitive_lab --token my-super-secret-token --retention 0
