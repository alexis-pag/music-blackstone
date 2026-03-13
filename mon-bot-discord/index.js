require('dotenv').config();
const { Client, GatewayIntentBits } = require('discord.js');

// Initialisation du client Discord avec les intentions nécessaires
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent
  ]
});

// Événement 'ready' qui s'exécute une seule fois au démarrage
client.once('ready', () => {
  console.log(`Connecté en tant que ${client.user.tag}`);
});

// Événement 'messageCreate' pour gérer les commandes
client.on('messageCreate', async (message) => {
  // On ignore les messages des autres bots ou les messages sans le préfixe '!'
  if (message.author.bot || !message.content.startsWith('!')) return;

  const command = message.content.slice(1).toLowerCase();

  // Commande !ping
  if (command === 'ping') {
    await message.reply('Pong !');
  }
});

// Connexion du bot via le token
client.login(process.env.DISCORD_TOKEN);
