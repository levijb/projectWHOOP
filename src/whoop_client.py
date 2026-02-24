# src/whoop_client.py
"""
Whoop API Client for data collection (v2 API)
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import time


class WhoopClient:
    """Client for interacting with Whoop API v2"""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.prod.whoop.com/developer/v2"
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers with authorization token"""
        return {
            "Authorization": f"Bearer {self.access_token}"
        }
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make authenticated request to Whoop API"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            print(f"✗ Request failed: {e}")
            print(f"  URL: {url}")
            if e.response is not None:
                print(f"  Response: {e.response.text}")
            return None
        except Exception as e:
            print(f"✗ Unexpected error: {e}")
            return None
    
    # User Profile
    def get_user_profile(self) -> Optional[Dict]:
        """Get basic user profile information"""
        return self._make_request("user/profile/basic")
    
    # Body Measurements
    def get_body_measurement(self) -> Optional[Dict]:
        """Get body measurements"""
        return self._make_request("user/measurement/body")
    
    # Cycle Collection
    def get_cycle_collection(self, start: datetime, end: datetime,
                            limit: int = 25, next_token: Optional[str] = None) -> Optional[Dict]:
        """Get daily cycle data for date range"""
        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": limit
        }
        
        if next_token:
            params["nextToken"] = next_token
        
        return self._make_request("cycle", params=params)
    
    def get_cycle_by_id(self, cycle_id: int) -> Optional[Dict]:
        """Get specific cycle by ID"""
        return self._make_request(f"cycle/{cycle_id}")
    
    # Recovery Collection
    def get_recovery_collection(self, start: datetime, end: datetime,
                               limit: int = 25, next_token: Optional[str] = None) -> Optional[Dict]:
        """Get recovery data for date range"""
        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": limit
        }
        
        if next_token:
            params["nextToken"] = next_token
        
        return self._make_request("recovery", params=params)
    
    # Sleep Collection
    def get_sleep_collection(self, start: datetime, end: datetime,
                            limit: int = 25, next_token: Optional[str] = None) -> Optional[Dict]:
        """Get sleep data for date range"""
        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": limit
        }
        
        if next_token:
            params["nextToken"] = next_token
        
        return self._make_request("activity/sleep", params=params)
    
    def get_sleep_by_id(self, sleep_id: str) -> Optional[Dict]:
        """Get specific sleep session by UUID"""
        return self._make_request(f"activity/sleep/{sleep_id}")
    
    # Workout Collection
    def get_workout_collection(self, start: datetime, end: datetime,
                              limit: int = 25, next_token: Optional[str] = None) -> Optional[Dict]:
        """Get workout data for date range"""
        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": limit
        }
        
        if next_token:
            params["nextToken"] = next_token
        
        return self._make_request("activity/workout", params=params)
    
    def get_workout_by_id(self, workout_id: str) -> Optional[Dict]:
        """Get specific workout by UUID"""
        return self._make_request(f"activity/workout/{workout_id}")
    
    # Pagination Helper
    def get_all_pages(self, endpoint_method, start: datetime, end: datetime, 
                     limit: int = 25) -> List[Dict]:
        """Get all pages of data for a paginated endpoint"""
        all_records = []
        next_token = None
        page = 1
        
        while True:
            print(f"  Fetching page {page}...")
            
            response = endpoint_method(start, end, limit, next_token)
            
            if not response:
                break
            
            records = response.get('records', [])
            all_records.extend(records)
            print(f"    Got {len(records)} records")
            
            next_token = response.get('next_token')
            if not next_token:
                break
            
            page += 1
            time.sleep(0.5)  # Rate limiting
        
        print(f"  Total: {len(all_records)} records")
        return all_records
    
    def get_all_historical_data(self, days_back: int = 180) -> Dict[str, List]:
        """Get all available historical data"""
        end = datetime.now()
        start = end - timedelta(days=days_back)
        
        print(f"\nFetching data from {start.date()} to {end.date()}")
        print("=" * 60)
        
        data = {}
        
        print("\n1. User Profile...")
        data['user'] = self.get_user_profile()
        if data['user']:
            print(f"   ✓ {data['user'].get('first_name')} {data['user'].get('last_name')}")
        
        print("\n2. Body Measurements...")
        data['body'] = self.get_body_measurement()
        if data['body']:
            print(f"   ✓ {data['body'].get('height_meter')}m, {data['body'].get('weight_kilogram')}kg")
        
        print("\n3. Cycles...")
        data['cycles'] = self.get_all_pages(self.get_cycle_collection, start, end)
        
        print("\n4. Recovery...")
        data['recovery'] = self.get_all_pages(self.get_recovery_collection, start, end)
        
        print("\n5. Sleep...")
        data['sleep'] = self.get_all_pages(self.get_sleep_collection, start, end)
        
        print("\n6. Workouts...")
        data['workouts'] = self.get_all_pages(self.get_workout_collection, start, end)
        
        print("\n" + "=" * 60)
        print("✓ Data collection complete!")
        
        return data