# Music-Blackstone

**Music-Blackstone** est un bot Discord de musique développé en **Python**.  
Son objectif principal est de permettre aux utilisateurs d’un serveur Discord **d’écouter de la musique provenant de YouTube directement dans un salon vocal**.

Le projet est **open source**, le code est **hébergé sur GitHub**, et il inclut également une **interface Web basée sur Flask** permettant de surveiller l’état du bot lorsqu’il est déployé sur **Render**.

---

# Objectif du projet

Le bot **Music-Blackstone** a pour but de :

- Lire de la musique provenant de **YouTube**
- Permettre aux utilisateurs de **contrôler la musique avec des commandes Discord**
- Gérer automatiquement **une file d'attente**
- Fonctionner sur **plusieurs serveurs Discord simultanément**
- Être **hébergé sur Render** avec une interface Web de monitoring
- Garder **tout le code et toutes les modifications synchronisés sur GitHub**

Le projet est entièrement public afin de faciliter **le suivi du développement et les contributions**.

---

# Fonctionnalités

### Lecture de musique
- Lecture de musique depuis **YouTube**
- Support des **liens et des recherches**
- Lecture continue via une file d'attente

### Gestion de la file d'attente
- Ajout automatique des musiques
- Affichage de la file d’attente
- Lecture automatique de la musique suivante

### Commandes du bot
- `!play` — lance une musique depuis YouTube
- `!queue` — affiche la file d'attente
- `!pause` — met la musique en pause
- `!resume` — reprend la lecture
- `!skip` — passe à la musique suivante
- `!stop` — arrête la musique et vide la file

### Support multi-serveurs
Chaque serveur Discord possède :

- sa propre file d’attente
- sa propre lecture musicale

### Interface Web

Une interface Web **Flask** est incluse pour :

- vérifier que le bot fonctionne
- maintenir le service actif sur **Render**
- surveiller l'état du bot

---

# Technologies utilisées

- **Python**
- **discord.py**
- **yt-dlp**
- **FFmpeg**
- **Flask**
- **Render**
- **GitHub**

---

# Gestion des modifications (Important)

Toutes les modifications apportées au projet **doivent être publiées sur GitHub** afin de :

- garder une **trace claire de l'évolution du projet**
- éviter la perte de code
- permettre la **collaboration**
- maintenir une **version stable du projet**

### Procédure recommandée après chaque modification

1. Ajouter les fichiers modifiés :

```bash
git add .