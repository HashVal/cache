#!usr/bin/env python

"""Test blockconnection"""

import time
import json
import pika
import sys

from threading import Thread, Event


#assume dispatcher doesn't start any queue
class listener(object):
    def __init__(self, broker="localhost"):
        """Gerrit Event Listener"""
        self.broker = broker
        self.gerrit_event = [6, 5, 8, 4, 3, 1]
        self.dispatcher = None
        self.workers = None

    def start(self):
        print "Start listener for testing."
        self.conn = pika.BlockingConnection(pika.ConnectionParameters(
            host=self.broker))
        print "Connection setup done!"
        self.channel = self.conn.channel()
        self.channel.queue_declare(queue='gerrit_event')
        print "listener queue declared, now start testing..."
        print "Start dispatcher and worker threads"
        self.dispatcher = dispatcher()
        self.dispatcher.start()
        print "Dispatcher ready! Start two workers!"
        self.workers = []
        self.workers.append(worker("worker_far", 2))
        self.workers.append(worker("worker_boo", 3))
        for w in self.workers:
            w.start()
            print "Worker %s ready!" %w.name
        print "Sleep 5 seconds then start testing!"
        time.sleep(5)
        print "Start testing!"
        self.test()

    def test(self):
        """Test JOB, each event sleep 10-event then publish it to channel"""
        print "listener start broadcast: Bruckner No.8 Symphony - Celibidache"
        for ge in self.gerrit_event:
            print "Current event: %d, so sleep (10-%d) seconds" %(ge, ge)
            time.sleep(10-ge)
            self.publish_event(ge)
        print "Dispatcher result as below!"
        for r in self.dispatcher.results:
            print str(r)

    def publish_event(self, value):
        _prop = pika.BasicProperties(content_type='application/json',)
        task = json.dumps({"value":value})
        print "Listener publish to dispatcher: %s" %str(task)
        self.channel.basic_publish(exchange='',
                                   routing_key='gerrit_event',
                                   properties=_prop,
                                   body=task)



class dispatcher(Thread):
    def __init__(self, broker="localhost"):
        Thread.__init__(self)
        print "Init dispatcher with broker: %s" %str(broker)
        self.broker = broker
        self.results = []
        self.go_far = True

    def run(self):
        print "Start dispatcher"
        self.conn = pika.BlockingConnection(pika.ConnectionParameters(
                                                host=self.broker))
        print "Connection setup done!"
        self.channel = self.conn.channel()
        self.channel.queue_declare(queue='worker_far')
        self.channel.queue_declare(queue='worker_boo')
        self.channel.queue_declare(queue='result')
        print "All Queue declared!"
        self.channel.basic_consume(self.process_result,
                                   queue='result', no_ack=True)
        self.channel.basic_consume(self.dispatch_event,
                                   queue='gerrit_event', no_ack=True)
        self.channel.start_consuming()

    def dispatch_event(self, ch, method, properties, event):
        e = json.loads(event)
        print "Dispatcher get event: %s" %str(e)
        time.sleep(1)
        if self.go_far:
            print "Dispatch to worker_far!"
            self.publish_job(e["value"], "worker_far")
            self.go_far = False
            print "Dispatch to worker_boo"
        else:
            self.publish_job(e["value"], "worker_boo")
            self.go_far = True
        return

    def process_result(self, ch, method, properties, job_record):
        job_result = json.loads(job_record)
        print "Raw job result: %s" %str(job_result)
        worker, result = job_result["worker"], job_result["result"]
        self.results.append((worker, result))
        print "Dispatcher get result: %s, %s" %(str(worker), str(result))
        return

    def publish_job(self, job, worker_name):
        _prop = pika.BasicProperties(content_type='application/json',)
        task = json.dumps({"job":job})
        self.channel.basic_publish(exchange='',
                                   routing_key=worker_name,
                                   properties=_prop,
                                   body=task)

class worker(Thread):
    """Worker Thread base class"""
    def __init__(self, name, multiply):
        """"""
        Thread.__init__(self)
        self.name = name
        self.multiply = multiply
        self.daemon = True
        self._stop = Event()
        self.broker = "localhost"

    def stop(self):
        """Stop worker thread"""
        self._stop.set()
        print "%s stopped as required." %self.name

    def run(self, block=True, timeout=None):
        """"""
        try:
            self.conn = pika.BlockingConnection(pika.ConnectionParameters(
                                                host=self.broker))
            self.channel = self.conn.channel()
            self.channel.basic_consume(self.act,
                                       queue=self.name, no_ack=True)
            self.channel.start_consuming()
        except Exception as e:
            print "%s : Error when run: %s" %(self.name, str(e))

    def act(self, ch, method, properties, job):
        j = json.loads(job)
        print "%s : Job dict unpack result: %s" %(self.name, j)
        print "%s : Sleep %s seconds, start" %(self.name, j["job"])
        time.sleep(int(j["job"]))
        print "%s : End of sleep, return %s * %s" %(self.name, self.multiply, j["job"])
        self.publish_result(int(j["job"])*self.multiply)

    def publish_result(self, result):
        _prop = pika.BasicProperties(content_type='application/json',)
        result_dict = json.dumps({"worker": self.name, "result":result})
        print "%s : Publish result: %s" %(self.name, result)
        self.channel.basic_publish(exchange='',
                                   routing_key="result",
                                   properties=_prop,
                                   body=result_dict)


if __name__ == "__main__":
    l = listener()
    l.start()
