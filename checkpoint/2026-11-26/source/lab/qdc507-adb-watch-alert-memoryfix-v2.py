#!/usr/bin/python3.8
import runpy
m=runpy.run_path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-v44-runtime.py')
m["entry"]('watch',__import__("sys").argv[1:])
