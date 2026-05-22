import os
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "projects_db")

client = AsyncIOMotorClient(MONGO_URI)

def get_database():
    return client[DB_NAME]

MAIN_DOCUMENT_ID = ObjectId("6794a46ec28cd5b59c70345b")
