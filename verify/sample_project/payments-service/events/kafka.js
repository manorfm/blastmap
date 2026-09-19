const { Kafka } = require('kafkajs');

const kafka = new Kafka({ clientId: 'payments-service', brokers: ['localhost:9092'] });

const producer = kafka.producer();
const consumer = kafka.consumer({ groupId: 'payments-service-group' });

async function startProducer() {
  await producer.connect();
}

async function publish(topic, payload) {
  await producer.send({
    topic,
    messages: [{ value: JSON.stringify(payload) }],
  });
}

async function startOrderCancelledConsumer(paymentService) {
  await consumer.connect();
  await consumer.subscribe({ topic: 'order.cancelled', fromBeginning: false });

  await consumer.run({
    eachMessage: async ({ message }) => {
      const event = JSON.parse(message.value.toString());
      await paymentService.refundCustomer(event.orderId, 'order_cancelled');
    },
  });
}

module.exports = { kafka, producer, consumer, startProducer, publish, startOrderCancelledConsumer };
