const mongoose = require('mongoose');

const transactionSchema = new mongoose.Schema({
  orderId: String,
  amount: Number,
  currency: String,
  paymentToken: String,
  status: String,
  fraudScore: Number,
  gatewayReference: String,
  createdAt: { type: Date, default: Date.now },
});

module.exports = mongoose.model('Transaction', transactionSchema);
