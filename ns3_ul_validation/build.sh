#!/bin/bash
set -e
MODULES="libns3.35-core libns3.35-network libns3.35-internet libns3.35-point-to-point libns3.35-applications libns3.35-flow-monitor"
CFLAGS=$(pkg-config --cflags $MODULES)
LIBS=$(pkg-config --libs $MODULES)
echo "Compiling mmas-ul-validation..."
g++ -std=c++17 -O2 $CFLAGS -o mmas-ul-validation mmas-ul-validation.cc $LIBS -Wl,--no-as-needed
echo "Compilation OK"
