#!/usr/bin/env bash

# Mise à jour des paquets et installation de ffmpeg (nécessaire pour la lecture audio)
apt-get update
apt-get install -y ffmpeg

# Installation des dépendances Python
pip install -r requirements.txt
