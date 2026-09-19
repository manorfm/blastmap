const axios = require('axios');

const BASE_URL = 'https://card-gateway.vendor.io';

async function charge(payload) {
  const response = await axios.post(`${BASE_URL}/v1/charge`, {
    amount: payload.amount,
    currency: payload.currency,
    payment_token: payload.paymentToken,
  });
  return response.data;
}

async function refund(payload) {
  const response = await axios.post(`${BASE_URL}/v1/refund`, {
    gateway_reference: payload.gatewayReference,
    amount: payload.amount,
  });
  return response.data;
}

module.exports = { charge, refund };
