const cardGatewayClient = require('../clients/card_gateway.client');
const notifyHubClient = require('../clients/notify_hub.client');
const Transaction = require('../models/transaction.model');
const LedgerEntry = require('../models/ledger_entry.model');
const { publish } = require('../events/kafka');

const FRAUD_AMOUNT_THRESHOLD = 5000;

// TODO: this class has grown to own validation, fraud scoring, the gateway
// call, ledger writes, eventing, AND notifications — candidate to split.
class PaymentService {
  validateChargeRequest(payload) {
    const errors = [];
    if (!payload.amount || payload.amount <= 0) errors.push('amount must be positive');
    if (!payload.currency) errors.push('currency is required');
    if (!payload.paymentToken) errors.push('payment_token is required');
    return { valid: errors.length === 0, errors };
  }

  scoreFraudRisk(payload) {
    let score = 0;
    if (payload.amount > FRAUD_AMOUNT_THRESHOLD) score += 60;
    if (payload.currency && payload.currency !== 'USD') score += 10;
    return { score, blocked: score >= 60 };
  }

  async chargeCustomer(payload) {
    const validation = this.validateChargeRequest(payload);
    if (!validation.valid) {
      return { ok: false, status: 400, error: validation.errors.join(', ') };
    }

    const fraud = this.scoreFraudRisk(payload);
    if (fraud.blocked) {
      await this.publishPaymentEvent('payment.failed', {
        orderId: payload.orderId,
        reason: 'fraud_block',
      });
      return { ok: false, status: 402, error: 'transaction blocked by fraud check' };
    }

    let gatewayResult;
    try {
      gatewayResult = await cardGatewayClient.charge(payload);
    } catch (err) {
      await this.publishPaymentEvent('payment.failed', {
        orderId: payload.orderId,
        reason: 'gateway_declined',
      });
      return { ok: false, status: 402, error: 'card declined' };
    }

    const transaction = await Transaction.create({
      orderId: payload.orderId,
      amount: payload.amount,
      currency: payload.currency,
      paymentToken: payload.paymentToken,
      status: 'charged',
      fraudScore: fraud.score,
      gatewayReference: gatewayResult.reference,
    });

    await this.recordLedgerEntry({
      transactionId: transaction.id,
      orderId: payload.orderId,
      type: 'charge',
      amount: payload.amount,
      currency: payload.currency,
      reason: 'checkout_charge',
    });

    await this.publishPaymentEvent('payment.completed', {
      orderId: payload.orderId,
      transactionId: transaction.id,
      amount: payload.amount,
    });

    await this.sendCustomerNotification({
      orderId: payload.orderId,
      template: 'payment_receipt',
      data: { amount: payload.amount, currency: payload.currency },
    });

    return { ok: true, status: 200, transactionId: transaction.id };
  }

  async refundCustomer(orderId, reason) {
    const transaction = await Transaction.findOne({ orderId }).sort({ createdAt: -1 });
    if (!transaction) {
      console.error(`refundCustomer: no transaction found for order ${orderId}`);
      return { ok: false, error: 'transaction not found' };
    }

    await cardGatewayClient.refund({
      gatewayReference: transaction.gatewayReference,
      amount: transaction.amount,
    });

    transaction.status = 'refunded';
    await transaction.save();

    await this.recordLedgerEntry({
      transactionId: transaction.id,
      orderId,
      type: 'refund',
      amount: transaction.amount,
      currency: transaction.currency,
      reason,
    });

    console.log(`refunded transaction ${transaction.id} for order ${orderId} (${reason})`);
    return { ok: true, transactionId: transaction.id };
  }

  async recordLedgerEntry(entry) {
    return LedgerEntry.create(entry);
  }

  async publishPaymentEvent(topic, payload) {
    await publish(topic, payload);
  }

  async sendCustomerNotification(payload) {
    return notifyHubClient.send(payload);
  }
}

module.exports = new PaymentService();
