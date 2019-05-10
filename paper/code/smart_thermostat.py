# I523 Course Project
# Smart Thermostat

#Import packages
import os
import sys
import time
import datetime
import pytz
import timezonefinder
import pyowm
import geocoder
import pandas as pd
from cassandra.cluster import Cluster
import atexit

def exit_handler():
		try:
			r1.off()
			r2.off()
			r3.off()
			print('\n\n *** Stopping program & shutting off system ***')
		except:
			raise

atexit.register(exit_handler)

#Import custom classes for sensors
import LCD
import light_sensor
import relay_switch
import temp_humid
import touch_sensor
import ds18b20

#########
#update this with the ip of your cassandra seeds
cassandra_contact_points = ['10.0.0.42']
#########


######################
# function to collect Weather Data
# Sources for this section:
# 	documentation for the open weather api python module: https://pyowm.readthedocs.io/en/latest/usage-examples.html#create-global-owm-object
# 	geocoder code copied from stackoverflow post by Apollo_LFB: https://stackoverflow.com/questions/24906833/get-your-location-through-python
# 	geocoder docs also used to understand the package usage: https://geocoder.readthedocs.io/
######################

g = geocoder.ip('me')

def get_current_weather(g):
	owm = pyowm.OWM('24a35d03fca238fc68c5a56406696e20')
	obs = owm.weather_at_coords(g.latlng[0],g.latlng[1])
	w = obs.get_weather()
	curr_weather_data = [w.get_reference_time(timeformat='date'), w.get_detailed_status(), w.get_temperature('fahrenheit')['temp']]
	return curr_weather_data


######################
# Classes to use sensors
# Sources for this section:
#   LCD + DHT11:  http://www.circuitbasics.com/how-to-set-up-the-dht11-humidity-sensor-on-the-raspberry-pi/
#   GPIO: https://tutorials-raspberrypi.com/raspberry-pi-control-relay-switch-via-gpio/
#   Touch_Callback: https://sourceforge.net/p/raspberry-gpio-python/wiki/Inputs/
######################

#################
# HARD CODE PINS FOR SENSORS
RELAY_PIN_1 = 16
RELAY_PIN_2 = 18
RELAY_PIN_3 = 22
TOUCH_PIN = 13
LIGHT_PIN = 11
TEMP_HUMID_PIN = 22 #This is the GPIO pin. Other pins set using BOARD
#################

lcd = LCD.LCD_Display(rs=37,e=35,data_pins=[33,31,29,23])
light = light_sensor.READ_LIGHT_SENSOR(pin=LIGHT_PIN)
temp_humid = temp_humid.READ_DHT11(pin=TEMP_HUMID_PIN)
r1 = relay_switch.relay_switch(pin=RELAY_PIN_1)
r2 = relay_switch.relay_switch(pin=RELAY_PIN_2)
r3 = relay_switch.relay_switch(pin=RELAY_PIN_3)
temperature = ds18b20.ds18b20()

def change_display():
	global display_num
	if display_num == 1:
		display_num = 0
	else:
		display_num = 1

touch = touch_sensor.touch_sensor(change_display, pin=TOUCH_PIN)

######################
# functions to send data to database
# Sources for this section:
#   code for pandas_factory function from: https://stackoverflow.com/questions/41247345/python-read-cassandra-data-into-pandas
#   documentation for cassandra cluster module: https://datastax.github.io/python-driver/api/cassandra/cluster.html
######################

def pandas_factory(colnames, rows):
	return pd.DataFrame(rows, columns=colnames)

def cassandra_query(keyspace, query, params=(), return_data=False, contact_points=['127.0.0.1'], port=9042):
	try:
		if return_data == True:
			cluster = Cluster( contact_points, port )
			session = cluster.connect( keyspace )
			session.row_factory = pandas_factory
			session.default_fetch_size = None
			rslt = session.execute( query )
			rslt_df = rslt._current_rows
			cluster.shutdown()
			return rslt_df
		else:
			cluster = Cluster( contact_points, port )
			session = cluster.connect( keyspace )
			session.execute( query, params )
			cluster.shutdown()
	except:
		raise
		#print('Data not loaded. Check for Error')

######################
# functions to adjust the temperature
# Sources for this section:
#   timezone offset: https://stackoverflow.com/questions/15742045/getting-time-zone-from-lat-long-coordinates
######################

def set_tolarance(start='06:00:00', end='23:00:00', main=1, secondary=5):
	start = datetime.datetime.strptime(start, '%H:%M:%S').hour
	end = datetime.datetime.strptime(end, '%H:%M:%S').hour
	#Adjust timezone
	tf = timezonefinder.TimezoneFinder()
	timezone_str = tf.certain_timezone_at(lat=g.latlng[0], lng=g.latlng[1])
	timezone = pytz.timezone(timezone_str)
	dt = datetime.datetime.utcnow()
	timezone.localize(dt)
	now = datetime.datetime.utcnow() + timezone.utcoffset(dt)
	now = now.hour

	# compare current time to start and end points
	if now <= start and now >= end:
		if light.get() == 0:  # Check if the lights are on.  Indicates if someone is active.
			return main
		else:
			return secondary
	else:
		return main


def thermostat_adjust(indoor_temp, outdoor_temp, desired_temp, status, sys_off=False, fan_on=False, tolarance=2):
	"""
	r1 = heat
	r2 = AC
	r3 = fan
	Note: the fan should always turn on with either heat or AC
	"""
	if sys_off == True:
		r1.off()
		r2.off()
		r3.off()
		status = 'SYS OFF'
		return status
	elif indoor_temp >= desired_temp + tolarance and indoor_temp < outdoor_temp:
		r3.on()
		r2.on()
		r1.off()
		status = 'AC ON'
		return status
	elif indoor_temp < desired_temp and indoor_temp < outdoor_temp:
		r3.off()
		r2.off()
		r1.off()
		status = 'SYS OFF'
		return status
	elif indoor_temp <= desired_temp - tolarance and indoor_temp > outdoor_temp:
		r3.on()
		r1.on()
		r2.off()
		status = 'HEAT ON'
		return status
	elif indoor_temp > desired_temp and indoor_temp > outdoor_temp:
		r3.off()
		r1.off()
		r2.off()
		status = 'SYS OFF'
		return status
	elif fan_on == True:
		r1.off()
		r2.off()
		r3.on()
		status = 'FAN ON'
		return status
	else:
		return status


######################
# Output
######################

if __name__ == '__main__':
	try:
		status = 'SYS OFF'
		display_num = 1 # Sets the starting display.  Number will change with button press
		while True:
			# Automatic timezone adjustment code modified from: https://stackoverflow.com/questions/15742045/getting-time-zone-from-lat-long-coordinates
			tf = timezonefinder.TimezoneFinder()
			timezone_str = tf.certain_timezone_at(lat=g.latlng[0], lng=g.latlng[1])
			timezone = pytz.timezone(timezone_str)
			dt = datetime.datetime.utcnow()
			timezone.localize(dt)
			now = datetime.datetime.utcnow() + timezone.utcoffset(dt)

			try:
				curr_weather = get_current_weather(g)
			except:
				curr_weather = [now,'ERROR',desired_temp]
			
			#get current status from status table
			status_query = 'SELECT * FROM therm_status'
			try:
				status_df = cassandra_query('smart_therm', status_query, return_data=True, contact_points=cassandra_contact_points, port=9042)
				desired_temp = status_df.iloc[0]['desired_temp']
				fan = status_df.iloc[0]['fan_on']
				sys_off = status_df.iloc[0]['sys_off']
				main = status_df.iloc[0]['main']
				secondary = status_df.iloc[0]['secondary']
			except:
				raise
				print('ERROR: using default settings')
				sys.stdout.flush() #used to ensure the ability to print to nohup.out
				desired_temp = 69.0
				fan = False
				sys_off = False
				main = 1.5
				secondary = 4


			# Environment data variables
			timeStampVal = curr_weather[0] + timezone.utcoffset(dt)
			condition = curr_weather[1]
			out_temp_f = curr_weather[2]
			in_humid = temp_humid.get(temp_measure='farenhiet')[0]
			in_temp_f = temperature.get()[1]
			
			# Adjust thermostat based on variables
			if in_temp_f is not None or out_temp_f is not None:
				output = thermostat_adjust(in_temp_f,out_temp_f,desired_temp=desired_temp,status=status,fan_on=fan,sys_off=sys_off,tolarance=set_tolarance(main=main,secondary=secondary))
				if output == status or output == 'NO CHANGE':
					pass
				else:
					status = output
					#print('Status: '+status+' Indoor Temp: '+str(in_temp_f)+' Time: '+str(now))
					#sys.stdout.flush() #used to ensure the ability to print to nohup.out
			else:
				pass

			# Update display based on latest data
			if display_num == 1:
				lcd.display_string('In Temp: ' + str(round(in_temp_f,1)) + 'F', pos=(0,0), clear='Y')
				lcd.display_string('In Humid: ' + str(in_humid) + '%', pos=(1,0), clear='N')
			else:
				lcd.display_string('Out Temp: ' + str(round(out_temp_f,1)) + 'F', pos=(0,0), clear='Y')
				lcd.display_string(str(condition[0:15]), pos=(1,0), clear='N')

			# Load data to database
			insert_data = '''
		            INSERT INTO therm_data (indoor_time, outdoor_time, out_condition, out_temp_f, in_temp_f, humidity, status)
	        	    VALUES (%s,%s,%s,%s,%s,%s,%s)
		            '''

			params = (now,str(timeStampVal),condition,out_temp_f,in_temp_f,in_humid,status)

			try:
				cassandra_query('smart_therm', insert_data, params, contact_points=cassandra_contact_points, port=9042)
			except:
				print('ERROR: data not loaded to cassandra database  '+str(now))
				sys.stdout.flush() #used to ensure the ability to print to nohup.out

			time.sleep(15)
	except KeyboardInterrupt:
		r1.off()
		r2.off()
		r3.off()
		print('\n\n *** Stopping program & shutting off system ***')
		sys.stdout.flush() #used to ensure the ability to print to nohup.out
		try:
			r1.off()
			r2.off()
			r3.off()
			sys.exit(0)
		except SystemExit:
			r1.off()
			r2.off()
			r3.off()
			os._exit(0)
