#!/usr/bin/env python
from operator import itemgetter
from time import sleep, time
from multiprocessing import cpu_count
from psutil import virtual_memory
from copy import deepcopy
import threading, json, os
from optparse import OptionParser
from subprocess import Popen

def format(s, **kwds): return s % kwds
def runJob(job):
  p = Popen(job["command"], shell=True)
  job["exit_code"] = os.waitpid(p.pid,0)[1]

def getFinalCommand(group, jobs, resources):
  if not "final" in group: group["final"] = deepcopy(jobs["final_per_group"])
  job = group.pop("final")
  job["jobid"]=group["name"]+"(final)"
  group["state"]="Done"
  jobs_results = group["name"]+"-results.json"
  ref = open(jobs_results, 'w')
  ref.write(json.dumps(group, indent=2, sort_keys=True, separators=(',',': ')))
  ref.close()
  resources["done_groups"]=resources["done_groups"]+1
  job["command"]=format(job["command"],group_name=group["name"],jobs_results=jobs_results)
  return job

def getJob(jobs, resources, order):
  pending_jobs = []
  pending_groups = [ g for g in jobs["jobs"] if g["state"]=="Pending" ]
  for group in pending_groups:
    if [ j for j in group["commands"] if j["state"]=="Running" ]: continue
    if not [ j for j in group["commands"] if j["state"]=="Pending" ]: return True,getFinalCommand(group, jobs, resources)
    for job in group["commands"]:
      if job["state"]=="Pending":
        if (job["rss"]<=resources["available"]["rss"]) and (job["cpu"]<=resources["available"]["cpu"]): pending_jobs.append(job)
        break
      if job["exit_code"]!=0: return True,getFinalCommand(group, jobs, resources)
  if not pending_jobs: return len(pending_groups)>0,{}
  sort_by = order
  if order=="dynamic":
    rss_v = 100.0*resources["available"]["rss"]/resources["total"]["rss"]
    cpu_v = 100.0*resources["available"]["cpu"]/resources["total"]["cpu"]
    sort_by = "rss" if rss_v>cpu_v else "cpu"
    print "Sort by ",sort_by,rss_v,"vs",cpu_v
  return True, sorted(pending_jobs,key=itemgetter(sort_by),reverse=True)[0]

def startJob(job, resources, thrds):
  job["state"]="Running"
  job["start_time"]=int(time())
  for pram in ["rss", "cpu"]: resources["available"][pram]=resources["available"][pram]-job[pram]
  t = threading.Thread(target=runJob, args=(job,))
  thrds[t]=job
  print "Run",len(thrds),job["jobid"],job["rss"],job["cpu"],job["time"],resources["available"]
  t.start()

def checkJobs(thrds, resources):
  done_thrds = []
  while not done_thrds: sleep(1) ; done_thrds = [ t for t in thrds if not t.is_alive() ]
  for t in done_thrds:
    job = thrds.pop(t)
    job["end_time"]=int(time())
    job["state"]="Done"
    job["exec_time"]=job["end_time"]-job["start_time"]
    resources["done_jobs"]=resources["done_jobs"]+1
    for pram in ["rss", "cpu"]: resources["available"][pram]=resources["available"][pram]+job[pram]
    print "Done",len(thrds),job["jobid"],job["exec_time"],job["exit_code"],resources["available"],"JOBS:",resources["done_jobs"],"/",resources["total_jobs"],"GROUPS:",resources["done_groups"],"/",resources["total_groups"]

def initJobs(jobs, resources, otype):
  if not "final" in jobs: jobs["final"]="true"
  if not "final_per_group" in jobs: jobs["final_per_group"]={"command": "true", "cpu": 1,  "rss": 1, "time" : 1}
  for env,value in jobs["env"].iteritems(): os.putenv(env,value)
  total_groups=0
  total_jobs=0
  for group in jobs["jobs"]:
    total_groups+=1
    group["state"]="Pending"
    cmd_count = len(group["commands"])
    for i in reversed(range(cmd_count)):
      total_jobs+=1
      job = group["commands"][i]
      for x in ["rss","cpu"]:
        for y in [x+"_avg", x+"_max"]:
          if not y in job: job[y]=job[x]
      print ">>",group["name"],job
      for x in [ "rss", "cpu" ]: print "  ",x,int(job[x]*100/job[x+"_max"]),int(job[x+"_avg"]*100/job[x+"_max"])
      if otype:
        for x in [ "rss", "cpu" ]: job[x] = job[ x + "_" + otype ] 
      job["state"]="Pending"
      job["exit_code"]=-1
      job["jobid"]=group["name"]+"(%s/%s)" % (i+1, cmd_count)
      if (i>0): group["commands"][i-1]["time"]=group["commands"][i-1]["time"]+job["time"]
      for item in ["rss", "cpu"]:
        if resources["total"][item]<job[item]: resources["total"][item]=job[item]+1
  resources["available"]=deepcopy(resources["total"])
  resources["total_groups"] = total_groups
  resources["total_jobs"] = total_jobs+total_groups
  print "Total Resources:",resources["available"]
  return jobs

if __name__ == "__main__":
  parser = OptionParser(usage="%prog [-m|--memory <memory>] [-c|--cpu <cpu>] [-j|--jobs <jobs-json-file>]")
  parser.add_option("-m", "--memory", dest="memory", default=100, type="int", help="Percentage of total memory available for jobs")
  parser.add_option("-c", "--cpu",    dest="cpu",    default=200, type="int", help="Percentage of total cpu available for jobs e.g. on a 8 core machine it can use 1600% cpu.")
  parser.add_option("-j", "--jobs",   dest="jobs",   default="jobs.json",     help="Json file path with groups/jobs to run")
  parser.add_option("-o", "--order",  dest="order",  default="dynamic",       help="Order the jobs based on selected criteria. Valid values are time|rss|cpu|dynamic. Default value dynamic")
  parser.add_option("-t", "--type",   dest="type",   default="",              help="Order type. Valid values are avg|max. Default value ''")
  opts, args = parser.parse_args()

  if opts.memory>200: opts.memory=200
  if opts.cpu>300:    opts.cpu=300
  if not opts.type in [ "", "avg", "max" ]: parser.error("Invalid -t|--type value '%s' provided." % opts.type)
  if not opts.order in ["dynamic", "time", "rss", "cpu"]: parser.error("Invalid -o|--order value '%s' provided." % opts.order)
  resources={"total": {"cpu" : cpu_count()*opts.cpu, "rss" : int(virtual_memory().total*opts.memory/100)}, "total_groups" : 0, "total_jobs" : 0, "done_groups" : 0, "done_jobs" : 0}
  jobs=initJobs(json.load(open(opts.jobs)), resources, opts.type)
  thrds={}
  wait_for_jobs = False
  has_jobs = True
  while has_jobs:
    if wait_for_jobs:
      wait_for_jobs = False
      checkJobs(thrds, resources)
    has_jobs, job = getJob(jobs,resources, opts.order)
    if job: startJob(job, resources, thrds)
    else:   wait_for_jobs = True
  while len(thrds): checkJobs(thrds, resources)
  os.system(jobs["final"])
