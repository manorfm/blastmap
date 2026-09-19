const mongoose = require('mongoose');

const ledgerEntrySchema = new mongoose.Schema({
  transactionId: String,
  orderId: String,
  type: String,
  amount: Number,
  currency: String,
  reason: String,
  createdAt: { type: Date, default: Date.now },
});

module.exports = mongoose.model('LedgerEntry', ledgerEntrySchema);
