#!/bin/bash
cd /mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/paper
pdflatex -interaction=nonstopmode main.tex 2>&1 | tail -20
