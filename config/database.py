from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

from config.settings import MONGO_URI, DB_NAME, MAIN_DOCUMENT_ID

client = AsyncIOMotorClient(MONGO_URI)

def get_database():
    return client[DB_NAME]

MAIN_DOCUMENT_ID = ObjectId(MAIN_DOCUMENT_ID)
