const express = require('express');
const mongoose = require('mongoose');
const paymentsRoutes = require('./routes/payments.routes');
const paymentService = require('./services/payment.service');
const { startProducer, startOrderCancelledConsumer } = require('./events/kafka');

const app = express();
app.use(express.json());
app.use('/', paymentsRoutes);

const MONGO_URL = process.env.MONGO_URL || 'mongodb://localhost:27017/payments';

async function start() {
  await mongoose.connect(MONGO_URL);
  await startProducer();
  await startOrderCancelledConsumer(paymentService);

  app.listen(3000, () => {
    console.log('payments-service listening on port 3000');
  });
}

start().catch((err) => {
  console.error('failed to start payments-service', err);
  process.exit(1);
});
