const express = require('express');
const { Kafka } = require('kafkajs');

const app = express();
app.use(express.json());

const kafka = new Kafka({ clientId: 'payments-service', brokers: ['localhost:9092'] });
const producer = kafka.producer();

app.post('/charge', (req, res) => {
  const authHeader = req.headers['authorization'];
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'missing bearer token' });
  }

  const { amount, currency, payment_token } = req.body;
  if (!amount || amount <= 0) {
    return res.status(400).json({ error: 'amount must be positive' });
  }

  const transactionId = 'txn_456';
  producer.send({
    topic: 'payment_completed',
    messages: [{ value: JSON.stringify({ transactionId, amount, currency }) }],
  });

  res.json({ transactionId, status: 'charged' });
});

app.listen(3000);
