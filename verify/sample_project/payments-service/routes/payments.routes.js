const express = require('express');
const router = express.Router();
const paymentService = require('../services/payment.service');
const Transaction = require('../models/transaction.model');

router.post('/charge', (req, res) => {
  const authHeader = req.headers['authorization'];
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'missing bearer token' });
  }

  paymentService
    .chargeCustomer(req.body)
    .then((result) => {
      if (!result.ok) {
        return res.status(result.status).json({ error: result.error });
      }
      res.json({ transactionId: result.transactionId, status: 'charged' });
    })
    .catch((err) => {
      console.error('charge failed', err);
      res.status(500).json({ error: 'internal error' });
    });
});

router.get('/payments/:id', (req, res) => {
  Transaction.findById(req.params.id)
    .then((transaction) => {
      if (!transaction) {
        return res.status(404).json({ error: 'transaction not found' });
      }
      res.json(transaction);
    })
    .catch((err) => {
      console.error('lookup failed', err);
      res.status(500).json({ error: 'internal error' });
    });
});

router.post('/payments/:id/refund', (req, res) => {
  const reason = (req.body && req.body.reason) || 'manual_refund';
  paymentService
    .refundCustomer(req.params.id, reason)
    .then((result) => {
      if (!result.ok) {
        return res.status(404).json({ error: result.error });
      }
      res.json({ transactionId: result.transactionId, status: 'refunded' });
    })
    .catch((err) => {
      console.error('refund failed', err);
      res.status(500).json({ error: 'internal error' });
    });
});

module.exports = router;
