from flask import Flask, json, render_template, request, jsonify
from requests.models import CaseInsensitiveDict
import googlemaps
from azure.cosmos import CosmosClient, PartitionKey
import requests
import json
import random
import math
import os
from flask import Flask, request, jsonify
from flask_cors import cross_origin, CORS

MAPS_BASE = 'https://maps.googleapis.com/maps/api/directions/json'
POLLUTION_BASE = 'http://api.openweathermap.org/data/2.5/air_pollution?'

MAPS_API_KEY = os.environ['MAPS_API_KEY']
OPEN_WEATHER_KEY = os.environ['OPEN_WEATHER_KEY']
CARBON_KEY = os.environ['CARBON_KEY']
DB_KEY = os.environ['DB_KEY']


def manage_db():
    endpoint = 'https://xyz.documents.azure.com:443/'
    database_name = 'greenway'
    container_users_name = 'users'

    client = CosmosClient(endpoint, DB_KEY)
    database = client.create_database_if_not_exists(id=database_name)

    Container_users = database.create_container_if_not_exists(
        id=container_users_name,
        partition_key=PartitionKey(path="/id")
    )
    return Container_users

container_users = manage_db()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
gmaps = googlemaps.Client(key=MAPS_API_KEY)


@app.route('/api/signup', methods=['POST'])
@cross_origin(supports_credentials=True)
def signup():
    input_json = request.get_json(force=True)
    username, mail, password = input_json['username'], input_json['mail'], input_json['password']
    newUser = {
        'id': str(username),
        'mail': mail,
        'password': password,
        'vehicles': []
    }
    try:
        item_response = container_users.read_item(item=username, partition_key=username)
        if item_response['id'] == username:
            dictToReturn = {"message": "User with this username already exists. Choose a different user id"}
            return jsonify(dictToReturn), 403
    except:
        container_users.create_item(body=newUser)
    dictToReturn = {"message": "success"}
    return jsonify(dictToReturn), 201


@app.route('/api/login', methods=['POST'])
@cross_origin(supports_credentials=True)
def login():
    input_json = request.get_json(force=True)
    username, password = str(input_json['username']), input_json['password']

    try:
        item_response = container_users.read_item(item=username, partition_key=username)
    except:
        return jsonify({"message": "The username you entered doesnot exist"}), 404

    if password != item_response['password']:
        return jsonify({"message": "Your username and password don't match"}), 401

    dictToReturn = {"message": "success"}
    return jsonify(dictToReturn), 201


@app.route('/getroutes', methods=["POST"])
@cross_origin(supports_credentials=True)
def route():
    input_json = request.get_json(force=True)
    orig, dest, vid = input_json['origin'], input_json['destination'], input_json['vid']
    origLat, origLong = orig[0], orig[1]
    destLat, destLong = dest[0], dest[1]
    directions_result = gmaps.directions((origLat, origLong), (destLat, destLong), mode="driving", alternatives=True)

    routes = []
    for route in directions_result:
        steps = route['legs'][0]['steps']
        route_info = {}
        new_route = []
        pollution_index = 0
        stepsx = int(math.log2(len(steps)))
        skipsteps = len(steps) // stepsx
        cnt = skipsteps
        stepsindex = []

        for step in steps:
            new_route.append(step['start_location'])

            if cnt == 0:
                cnt = skipsteps
                uri = POLLUTION_BASE + f"lat={step['start_location']['lat']}&lon={step['start_location']['lng']}&appid={OPEN_WEATHER_KEY}"
                response = requests.get(uri)
                response = json.loads(response.text)
                stepsindex.append(int(response['list'][0]['main']['aqi']))

            cnt -= 1

        new_route.append({"lat": destLat, "lng": destLong})
        route_info['index'] = sum(stepsindex)//len(stepsindex) + random.random() - 1
        route_info['steps'] = new_route
        route_info['time'] = route['legs'][0]['duration']['text']
        route_info['dist'] = route['legs'][0]['distance']['text']

        if len(vid) > 0:
            carbinurl = "https://www.carboninterface.com/api/v1/estimates"

            headers = CaseInsensitiveDict()
            headers["Authorization"] = f"Bearer {CARBON_KEY}"
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "application/json"
            data = {
                "type": "vehicle",
                "distance_unit": "km",
                "distance_value": route_info['dist'],
                "vehicle_model_id": vid
            }

            data = json.dumps(data, indent=4)
            response = requests.post(carbinurl, headers=headers, data=data)
            data = json.loads(response.text)

            route_info['vindex'] = data['data']['attributes']['carbon_kg']
        else:
            route_info['vindex'] = 0

        routes.append(route_info)

    response = jsonify({"data": routes})
    return response, 200


# get all users
@app.route('/allusers', methods=["GET"])
def allusers():
    query = "SELECT * FROM c"
    allusers = list(container_users.query_items(query=query, enable_cross_partition_query=True))
    return jsonify({"data": allusers}), 200


# add a vehicle
@app.route('/addvehicle', methods=["POST"])
def addvehicle():
    input_json = request.get_json(force=True)
    username, vid, make, model = str(input_json['username']), input_json['vid'], input_json['make'], input_json['model']

    veh = {
        "vid":vid,
        "make":make,
        "model":model
    }

    try:
        user = container_users.read_item(item=username, partition_key=username)
        user['vehicles'].append(veh)
        container_users.replace_item(username, user, populate_query_metrics=None, pre_trigger_include=None,
                                     post_trigger_include=None)
    except:
        return jsonify({"message": "The username you entered doesnot exist"}), 404

    return jsonify({"message": "Vehicle added successfully"}), 201


# remove a vehicle
@app.route('/removevehicle', methods=["POST"])
def removevehicle():
    input_json = request.get_json(force=True)
    username, vid = str(input_json['username']), input_json['vid']

    try:
        user = container_users.read_item(item=username, partition_key=username)
        if vid not in user['vehicles']:
            return jsonify({"message": "Not found"}), 404

        user['vehicles'].remove(vid)
        container_users.replace_item(username, user, populate_query_metrics=None, pre_trigger_include=None,
                                     post_trigger_include=None)
    except:
        return jsonify({"message": "The username you entered doesnot exist"}), 404

    return jsonify({"message": "Vehicle removed successfully"}), 201


# my vehicles
@app.route('/myvehicles', methods=["POST"])
def myvehicles():
    input_json = request.get_json(force=True)
    username = str(input_json['username'])

    try:
        user = container_users.read_item(item=username, partition_key=username)

    except:
        return jsonify({"message": "The username you entered doesnot exist"}), 404

    return jsonify({"data": user['vehicles']}), 201


# vehicle combustion estimate
@app.route('/vehicleestimate', methods=["POST"])
def vehicleestimate():
    input_json = request.get_json(force=True)
    vid, dist, unit = str(input_json['vid']), input_json['dist'], input_json['unit']
    uri = "https://www.carboninterface.com/api/v1/estimates"

    headers = CaseInsensitiveDict()
    headers["Authorization"] = f"Bearer {CARBON_KEY}"
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"

    data = {
        "type": "vehicle",
        "distance_unit": unit,
        "distance_value": dist,
        "vehicle_model_id": vid
    }

    data = json.dumps(data, indent=4)
    response = requests.post(uri, headers=headers, data=data)
    data = json.loads(response.text)

    return jsonify({"data": data['data']['attributes']['carbon_kg']}), 201


# get user info
@app.route('/userinfo', methods=["POST"])
def userinfo():
    input_json = request.get_json(force=True)
    username = input_json['username']

    try:
        user = container_users.read_item(item=username, partition_key=username)
        data = {
            "username":user['id'],
            "mail":user['mail'],
            "vehicles":user['vehicles']
        }

        return jsonify({"data":data}), 200

    except:
        return jsonify({"message": "The username you entered doesnot exist"}), 404
