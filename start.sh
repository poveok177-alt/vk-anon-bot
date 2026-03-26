#!/bin/bash
# Запускаем бота в фоновом режиме
python3 main.py &
# Запускаем простейший сервер на порту, который требует Render
python3 -m http.server $PORT