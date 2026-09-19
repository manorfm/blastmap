const axios = require('axios');

const BASE_URL = 'https://notify-hub.vendor.io';

// Note: orders-service also calls notify-hub independently for its own
// order-status notifications. Duplicated integration, not shared here.
async function send(payload) {
  const response = await axios.post(`${BASE_URL}/v1/send`, {
    channel: 'email',
    template: payload.template,
    recipientId: payload.orderId,
    data: payload.data,
  });
  return response.data;
}

module.exports = { send };
